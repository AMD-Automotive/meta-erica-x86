// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * RAVE2 DRM/KMS Simple Pipeline Driver
 *
 * Copyright (C) 2026, Advanced Micro Devices, Inc.
 */

#include <linux/dmaengine.h>
#include <linux/hrtimer.h>
#include <linux/ktime.h>
#include <linux/mailbox_client.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/version.h>

#include <drm/drm_atomic.h>
#include <drm/drm_atomic_helper.h>
#include <drm/drm_drv.h>
#include <drm/drm_framebuffer.h>
#include <drm/drm_fourcc.h>
#include <drm/drm_gem_atomic_helper.h>
#include <drm/drm_gem_framebuffer_helper.h>
#include <drm/drm_gem_shmem_helper.h>
#include <drm/drm_modeset_helper_vtables.h>
#include <drm/drm_probe_helper.h>
#include <drm/drm_simple_kms_helper.h>
#include <drm/drm_vblank.h>

#define RAVE_KMS_FB_OFFSET	(0x800000ULL) /* 8M */

#define RAVE_MQ_HI_MAGIC	(0xBADA << 16)
#define RAVE_MQ_BUFFER_MAGIC	(RAVE_MQ_HI_MAGIC | 0xB00F)
#define RAVE_MQ_MESSAGE_MAGIC	(RAVE_MQ_HI_MAGIC | 0xB00E)

enum rave_mq_msg_type {
	RAVE_MQ_MSG_START,
	RAVE_MQ_MSG_STOP,
	RAVE_MQ_MSG_PAUSE,
	RAVE_MQ_MSG_PAGE_FLIP,
	RAVE_MQ_MSG_VBLANK,
	RAVE_MQ_MSG_KEEP_ALIVE,
};

/**
 * struct rave_mq_msg - Message structure for RAVE MQ
 * @magic: Magic number for message validation
 * @type: Message type identifier
 * @payload: 64-bit payload data
 */
struct rave_mq_msg {
	u32	magic;
	u32	type;
	u64	payload;
} __packed;

struct rave_kms {
	struct device *dev;
	struct drm_device drm;
	struct drm_simple_display_pipe pipe;
	struct drm_connector connector;

	/* DMA client stuff */
	struct device *dma_dev;
	struct dma_chan *dma_chan;
	struct sg_table *sgt;
	dma_addr_t dma_dest;
	ktime_t dma_start;

	/* Remote FB mem */
	dma_addr_t fb_mem_start;
	u64 fb_mem_size;
	u32 fb_id_start;

	/* Mbox client */
	struct mbox_client	mbox_client;
	struct mbox_chan	*mbox_chan;

	/* FIXME: poor man's vsync */
	ktime_t period;
	struct hrtimer timer;
};

static inline struct rave_kms *drm_to_rave_kms(struct drm_device *drm)
{
	return container_of(drm, struct rave_kms, drm);
}

static inline struct rave_kms *
pipe_to_rave_kms(struct drm_simple_display_pipe *pipe)
{
	return container_of(pipe, struct rave_kms, pipe);
}

static inline struct rave_kms *timer_to_rave_kms(struct hrtimer *timer)
{
	return container_of(timer, struct rave_kms, timer);
}

static enum hrtimer_restart rave_kms_timer_cb(struct hrtimer * timer)
{
	struct rave_kms *rkms = timer_to_rave_kms(timer);

	drm_crtc_handle_vblank(&rkms->pipe.crtc);
	hrtimer_forward_now(&rkms->timer, rkms->period);

	return HRTIMER_RESTART;
}

static int rave_kms_conn_get_modes(struct drm_connector *connector)
{
	struct drm_device *drm = connector->dev;
	struct drm_display_mode *mode;

	mode = drm_cvt_mode(drm, 1920, 1080, 60, false, false, false);
	if (!mode)
		return -ENOMEM;

	mode->type = DRM_MODE_TYPE_DRIVER | DRM_MODE_TYPE_PREFERRED;
	drm_mode_probed_add(connector, mode);

	connector->display_info.width_mm = 530;
	connector->display_info.height_mm = 300;

	return 1;
}

static const struct drm_connector_helper_funcs rave_kms_conn_helper_funcs = {
	.get_modes = rave_kms_conn_get_modes,
};

static const struct drm_connector_funcs rave_kms_conn_funcs = {
	.reset = drm_atomic_helper_connector_reset,
	.fill_modes = drm_helper_probe_single_connector_modes,
	.destroy = drm_connector_cleanup,
	.atomic_duplicate_state = drm_atomic_helper_connector_duplicate_state,
	.atomic_destroy_state = drm_atomic_helper_connector_destroy_state,
};

static int rave_kms_pipe_check(struct drm_simple_display_pipe *pipe,
			       struct drm_plane_state *plane_state,
			       struct drm_crtc_state *crtc_state)
{
	return 0;
}

static void rave_kms_pipe_enable(struct drm_simple_display_pipe *pipe,
				 struct drm_crtc_state *crtc_state,
				 struct drm_plane_state *plane_state)
{
	drm_crtc_vblank_on(&pipe->crtc);
}

static void rave_kms_pipe_disable(struct drm_simple_display_pipe *pipe)
{
	struct rave_kms *rkms = pipe_to_rave_kms(pipe);
	struct drm_crtc *crtc = &pipe->crtc;

	dmaengine_terminate_sync(rkms->dma_chan);

	spin_lock_irq(&crtc->dev->event_lock);
	if (crtc->state->event) {
		drm_crtc_send_vblank_event(crtc, crtc->state->event);
		crtc->state->event = NULL;
	}
	spin_unlock_irq(&crtc->dev->event_lock);

	drm_crtc_vblank_off(crtc);
}

static dma_addr_t rave_kms_fb_target_address(struct rave_kms *rkms,
					     struct drm_framebuffer *fb)
{
	if (!rkms->fb_id_start || fb->base.id < rkms->fb_id_start)
		rkms->fb_id_start = fb->base.id;

	return RAVE_KMS_FB_OFFSET * (fb->base.id - rkms->fb_id_start) +
	       rkms->fb_mem_start;
}

static void rave_kms_dma_result_cb(void *param,
				   const struct dmaengine_result *result)
{
	struct rave_kms *rkms = param;
	struct rave_mq_msg flip_msg = {
		.magic = RAVE_MQ_MESSAGE_MAGIC,
		.type = RAVE_MQ_MSG_PAGE_FLIP,
		.payload = rkms->dma_dest,
	};
	int ret;
	s64 dma_ns = ktime_to_ns(ktime_sub(ktime_get(), rkms->dma_start));

	dev_dbg(rkms->dev, "DMA transfer (%d chunks) completed in %lld us\n",
		rkms->sgt->nents, dma_ns / 1000);
	rkms->sgt = NULL;

	if (rkms->mbox_chan) {
		ret = mbox_send_message(rkms->mbox_chan, &flip_msg);
		if (ret < 0)
			dev_warn(rkms->dev, "mbox send failed: %d\n", ret);
		else
			mbox_client_txdone(rkms->mbox_chan, 0);
	}

	if (result->result != DMA_TRANS_NOERROR)
		dev_warn(rkms->dev, "dma xfer result = %d\n", result->result);
}

static void rave_kms_pipe_update(struct drm_simple_display_pipe *pipe,
				 struct drm_plane_state *old_plane_state)
{
	struct rave_kms *rkms = pipe_to_rave_kms(pipe);
	struct drm_crtc *crtc = &pipe->crtc;
	struct dma_slave_config dma_cfg = { 0 };
	struct drm_plane_state *new_plane_state = pipe->plane.state;
	struct drm_framebuffer *fb = new_plane_state->fb;
	struct drm_gem_object *gem;
	struct drm_gem_shmem_object *shmem;
	struct dma_async_tx_descriptor *desc;
	struct sg_table	*sgt;
	dma_cookie_t cookie;
	int ret;

	if (crtc->state->event) {
		struct drm_pending_vblank_event *vblank = crtc->state->event;

		crtc->state->event = NULL;

		spin_lock_irq(&crtc->dev->event_lock);
		if (crtc->state->active && drm_crtc_vblank_get(crtc) == 0)
			drm_crtc_arm_vblank_event(crtc, vblank);
		else
			drm_crtc_send_vblank_event(crtc, vblank);
		spin_unlock_irq(&crtc->dev->event_lock);
	}

	if (fb && fb != old_plane_state->fb) {
		rkms->dma_dest = rave_kms_fb_target_address(rkms, fb);
		dma_cfg.direction = DMA_MEM_TO_DEV;
		dma_cfg.dst_addr = rkms->dma_dest;

		gem = drm_gem_fb_get_obj(fb, 0);
		shmem = to_drm_gem_shmem_obj(gem);

		/*
		 * The scanout buffer may either be a native shmem dumb buffer
		 * (e.g. Weston's Pixman renderer) or a dma-buf imported from
		 * another GPU (e.g. amdgpu when Weston composites with GL and
		 * scans out here as a secondary device). For imported objects
		 * the backing sg_table is already set up at import time, and
		 * drm_gem_shmem_get_pages_sgt() must not be called on them
		 * (it WARNs and returns -EINVAL). Pick the right source.
		 */
		if (drm_gem_is_imported(gem))
			sgt = shmem->sgt;
		else
			sgt = drm_gem_shmem_get_pages_sgt(shmem);

		if (IS_ERR_OR_NULL(sgt)) {
			dev_err(rkms->dev, "failed to get sg_table: %ld\n",
				PTR_ERR(sgt));
			return;
		}

		if (rkms->sgt) {
			s64 dma_ns = ktime_to_ns(ktime_sub(ktime_get(),
						 rkms->dma_start));
			dev_warn(rkms->dev, "dma engine busy after %lld us\n",
				 dma_ns / 1000);
			return;
		}

		ret = dmaengine_slave_config(rkms->dma_chan, &dma_cfg);
		if (ret) {
			dev_err(rkms->dev, "failed to configure dma channel: %d\n",
				ret);
			return;
		}

		desc = dmaengine_prep_slave_sg(rkms->dma_chan, sgt->sgl,
					       sgt->nents,
					       DMA_MEM_TO_DEV,
					       DMA_PREP_INTERRUPT);
		if (!desc) {
			dev_err(rkms->dev, "failed to prepare dma xfer\n");
			return;
		}

		desc->callback_result = rave_kms_dma_result_cb;
		desc->callback_param = rkms;

		cookie = dmaengine_submit(desc);
		ret = dma_submit_error(cookie);
		if (ret) {
			dev_err(rkms->dev, "failed to submit dma descriptor: %d\n",
				ret);
			return;
		}

		rkms->sgt = sgt;
		rkms->dma_start = ktime_get();
		dma_async_issue_pending(rkms->dma_chan);
	}
}

static int rave_kms_pipe_enable_vblank(struct drm_simple_display_pipe *pipe)
{
	struct rave_kms *rkms = pipe_to_rave_kms(pipe);

	hrtimer_start(&rkms->timer, rkms->period, HRTIMER_MODE_REL);

	return 0;
}

static void rave_kms_pipe_disable_vblank(struct drm_simple_display_pipe *pipe)
{
	struct rave_kms *rkms = pipe_to_rave_kms(pipe);

	hrtimer_cancel(&rkms->timer);
}

static const struct drm_simple_display_pipe_funcs rave_kms_pipe_funcs = {
	.check = rave_kms_pipe_check,
	.enable = rave_kms_pipe_enable,
	.disable = rave_kms_pipe_disable,
	.update = rave_kms_pipe_update,
	.enable_vblank = rave_kms_pipe_enable_vblank,
	.disable_vblank = rave_kms_pipe_disable_vblank,
	DRM_GEM_SIMPLE_DISPLAY_PIPE_SHADOW_PLANE_FUNCS,
};

static const struct drm_mode_config_funcs rave_kms_mode_config_funcs = {
	.fb_create = drm_gem_fb_create,
	.atomic_check = drm_atomic_helper_check,
	.atomic_commit = drm_atomic_helper_commit,
};

DEFINE_DRM_GEM_FOPS(rave_kms_fops);

static struct drm_gem_object *rave_kms_gem_prime_import(struct drm_device *drm,
							struct dma_buf *dma_buf)
{
	struct rave_kms *rkms = drm_to_rave_kms(drm);

	return drm_gem_prime_import_dev(drm, dma_buf, rkms->dma_dev);
}

static const struct drm_driver rave_kms_driver = {
	.driver_features = DRIVER_MODESET | DRIVER_GEM | DRIVER_ATOMIC,
	.fops = &rave_kms_fops,
	.name = "rave-kms",
	.desc = "RAVE2 Simple KMS",
	.major = 1,
	.minor = 0,
	DRM_GEM_SHMEM_DRIVER_OPS,
	/*
	 * Override the shmem default import. DRM_GEM_SHMEM_DRIVER_OPS sets
	 * .gem_prime_import to drm_gem_shmem_prime_import_no_map(), which
	 * imports dma-bufs without mapping their sg_table (shmem->sgt stays
	 * NULL). rave-kms DMAs directly from the framebuffer's sg_table, so we
	 * must map imported buffers against our DMA engine device. Provide the
	 * mapping import path and an explicit sg_table importer (the macro no
	 * longer sets .gem_prime_import_sg_table, which drm_gem_prime_import_dev
	 * requires). These initializers come after the macro so they win.
	 */
	.gem_prime_import = rave_kms_gem_prime_import,
	.gem_prime_import_sg_table = drm_gem_shmem_prime_import_sg_table,
};

static int rave_kms_probe(struct platform_device *pdev)
{
	static const u32 formats[] = {
		DRM_FORMAT_XRGB8888,
	};
	struct device *dev = &pdev->dev;
	struct rave_kms *rkms;
	int ret;

	rkms = devm_drm_dev_alloc(dev, &rave_kms_driver,
				  struct rave_kms, drm);
	if (!rkms)
		return -ENOMEM;

	rkms->dev = dev;
	platform_set_drvdata(pdev, rkms);

	/* Set VSYNC timer */
	rkms->period = ktime_set(0, NSEC_PER_SEC / 60);
	hrtimer_setup(&rkms->timer, rave_kms_timer_cb, CLOCK_MONOTONIC,
		      HRTIMER_MODE_REL);

	/* Map remote FB memory */
	ret = of_property_read_u64_index(rkms->dev->of_node, "amd,fb-mem", 0,
					 &rkms->fb_mem_start);
	if (ret)
		return dev_err_probe(rkms->dev, ret,
				     "amd,fb-mem property missing\n");

	ret = of_property_read_u64_index(rkms->dev->of_node, "amd,fb-mem", 1,
					 &rkms->fb_mem_size);
	if (ret)
		return dev_err_probe(rkms->dev, ret, "amd,fb-mem too short\n");

	/* Setup DMA channel */
	rkms->dma_chan = dma_request_chan(rkms->dev, "h2c");
	if (IS_ERR(rkms->dma_chan))
		return dev_err_probe(rkms->dev, PTR_ERR(rkms->dma_chan),
				     "failed to request dma\n");

	rkms->dma_dev = dmaengine_get_dma_device(rkms->dma_chan);

	/* Setup mailbox client */
	rkms->mbox_client.dev = rkms->dev;
	rkms->mbox_client.tx_block = false;
	rkms->mbox_client.knows_txdone = true;
	rkms->mbox_chan = mbox_request_channel(&rkms->mbox_client, 0);
	if (IS_ERR(rkms->mbox_chan)) {
		if (PTR_ERR(rkms->mbox_chan) == -EPROBE_DEFER) {
			ret = -EPROBE_DEFER;
			rkms->mbox_chan = NULL;
			goto err_mbox;
		}
		dev_warn(rkms->dev, "failed to request mbox channel: %ld\n",
			 PTR_ERR(rkms->mbox_chan));
		rkms->mbox_chan = NULL;
		/* Not fatal, we'll just miss page flip events */
	}

	/* Initialize DRM/KMS subsystem */
	ret = drmm_mode_config_init(&rkms->drm);
	if (ret)
		goto err_mbox;

	rkms->drm.mode_config.funcs= &rave_kms_mode_config_funcs;
	rkms->drm.mode_config.min_width = 1920;
	rkms->drm.mode_config.min_height = 1080;
	rkms->drm.mode_config.max_width = 1920;
	rkms->drm.mode_config.max_height = 1080;

	ret = drm_connector_init(&rkms->drm, &rkms->connector,
				 &rave_kms_conn_funcs,
				 DRM_MODE_CONNECTOR_VIRTUAL);
	if (ret)
		goto err_mbox;

	drm_connector_helper_add(&rkms->connector,
				 &rave_kms_conn_helper_funcs);

	ret = drm_simple_display_pipe_init(&rkms->drm, &rkms->pipe,
					   &rave_kms_pipe_funcs,
					   formats, ARRAY_SIZE(formats),
					   NULL, &rkms->connector);
	if (ret)
		goto err_connector;

	ret = drm_vblank_init(&rkms->drm, 1);
	if (ret)
		goto err_connector;

	drm_mode_config_reset(&rkms->drm);

	ret = drm_dev_register(&rkms->drm, 0);
	if (ret)
		goto err_connector;

	return 0;

err_connector:
	drm_connector_cleanup(&rkms->connector);

err_mbox:
	if (rkms->mbox_chan)
		mbox_free_channel(rkms->mbox_chan);
	dma_release_channel(rkms->dma_chan);

	return ret;
}

static void rave_kms_remove(struct platform_device *pdev)
{
	struct rave_kms *rkms = platform_get_drvdata(pdev);

	drm_dev_unregister(&rkms->drm);
	drm_atomic_helper_shutdown(&rkms->drm);

	if (rkms->mbox_chan)
		mbox_free_channel(rkms->mbox_chan);

	dmaengine_terminate_sync(rkms->dma_chan);
	dma_release_channel(rkms->dma_chan);
}

static const struct of_device_id rave_kms_of_match[] = {
	{ .compatible = "amd,rave-kms-1.0" },
	{}
};
MODULE_DEVICE_TABLE(of, rave_kms_of_match);

static struct platform_driver rave_kms_platform_driver = {
	.probe = rave_kms_probe,
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 11, 0)
	.remove = rave_kms_remove,
#else
	.remove_new = rave_kms_remove,
#endif
	.driver = {
		.name = "rave-kms",
		.of_match_table = rave_kms_of_match,
	},
};
module_platform_driver(rave_kms_platform_driver);

MODULE_AUTHOR("Anatoliy Klymenko");
MODULE_DESCRIPTION("RAVE2 DRM/KMS simple pipeline driver");
MODULE_LICENSE("GPL");
