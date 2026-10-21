// SPDX-License-Identifier: MIT
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: BW
// Maintainer: BW
/*
 * camera_dmabuf.c - Zero-copy multi-camera V4L2 -> DRM/KMS tiled display.
 *
 * The dmabuf path for pcie-camera-passthrough-simple. Extends the single-camera
 * zero-copy demo
 * to N channels: each camera gets its own NV12 dumb buffers allocated on the
 * GPU, exported as dmabuf and imported into V4L2 (DMABUF memory mode) so the
 * PCIe DMA writes directly into GPU-scannable memory. Each camera is displayed
 * on its own DRM overlay plane, scaled into a tile of a uniform grid, taking
 * over the whole display -- true zero-copy per channel.
 *
 * Tiling matches the mmap path: cols = ceil(sqrt(n)), rows = ceil(n/cols)
 * (1->1x1, 4->2x2, 5->3x2).
 *
 * Requires IOMMU enabled for the PCIe DMA engine to access GPU GTT memory.
 * Uses only raw kernel ioctls (no libdrm required).
 *
 * Usage: ./camera_dmabuf <card> <config> <dev0> [dev1 ...]
 *   card   : /dev/dri/cardN
 *   config : camera config (0/1/2) - informational; layout follows dev count
 *   devN   : /dev/videoN capture nodes
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <signal.h>
#include <math.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <stdint.h>
#include <linux/videodev2.h>
#include <drm/drm.h>
#include <drm/drm_mode.h>
#include <drm/drm_fourcc.h>

#define NUM_BUFS   4
#define MAX_CAMS   8

static volatile int running = 1;

static void sighandler(int sig)
{
	(void)sig;
	running = 0;
}

static int xioctl(int fd, unsigned long req, void *arg)
{
	int r;
	do { r = ioctl(fd, req, arg); } while (r == -1 && errno == EINTR);
	return r;
}

/* ---- DRM helpers ---- */

static int drm_set_client_cap(int fd, uint64_t cap, uint64_t value)
{
	struct drm_set_client_cap c = { .capability = cap, .value = value };
	return xioctl(fd, DRM_IOCTL_SET_CLIENT_CAP, &c);
}

static int drm_get_resources(int fd, struct drm_mode_card_res *res,
			     uint32_t *conn_ids, uint32_t *crtc_ids,
			     uint32_t max_conn, uint32_t max_crtc)
{
	memset(res, 0, sizeof(*res));
	if (xioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, res) < 0)
		return -1;
	if (res->count_connectors > max_conn)
		res->count_connectors = max_conn;
	if (res->count_crtcs > max_crtc)
		res->count_crtcs = max_crtc;
	res->connector_id_ptr = (uint64_t)(uintptr_t)conn_ids;
	res->crtc_id_ptr = (uint64_t)(uintptr_t)crtc_ids;
	res->fb_id_ptr = 0;
	res->encoder_id_ptr = 0;
	res->count_fbs = 0;
	res->count_encoders = 0;
	return xioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, res);
}

static int drm_get_connector(int fd, uint32_t conn_id,
			     struct drm_mode_get_connector *conn,
			     struct drm_mode_modeinfo *modes, uint32_t max_modes)
{
	memset(conn, 0, sizeof(*conn));
	conn->connector_id = conn_id;
	if (xioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, conn) < 0)
		return -1;
	if (conn->count_modes > max_modes)
		conn->count_modes = max_modes;
	conn->modes_ptr = (uint64_t)(uintptr_t)modes;
	conn->props_ptr = 0;
	conn->prop_values_ptr = 0;
	conn->encoders_ptr = 0;
	conn->count_props = 0;
	conn->count_encoders = 0;
	return xioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, conn);
}

static int drm_get_encoder(int fd, uint32_t enc_id,
			   struct drm_mode_get_encoder *enc)
{
	memset(enc, 0, sizeof(*enc));
	enc->encoder_id = enc_id;
	return xioctl(fd, DRM_IOCTL_MODE_GETENCODER, enc);
}

static int drm_get_crtc(int fd, uint32_t crtc_id, struct drm_mode_crtc *crtc)
{
	memset(crtc, 0, sizeof(*crtc));
	crtc->crtc_id = crtc_id;
	return xioctl(fd, DRM_IOCTL_MODE_GETCRTC, crtc);
}

static int drm_get_plane_resources(int fd, uint32_t *plane_ids,
				   uint32_t max_planes, uint32_t *count_out)
{
	struct drm_mode_get_plane_res res;
	memset(&res, 0, sizeof(res));
	if (xioctl(fd, DRM_IOCTL_MODE_GETPLANERESOURCES, &res) < 0)
		return -1;
	*count_out = res.count_planes;
	if (res.count_planes > max_planes)
		res.count_planes = max_planes;
	res.plane_id_ptr = (uint64_t)(uintptr_t)plane_ids;
	if (xioctl(fd, DRM_IOCTL_MODE_GETPLANERESOURCES, &res) < 0)
		return -1;
	*count_out = res.count_planes;
	return 0;
}

static int drm_get_plane(int fd, uint32_t plane_id,
			 struct drm_mode_get_plane *plane,
			 uint32_t *fmts, uint32_t max_fmts)
{
	memset(plane, 0, sizeof(*plane));
	plane->plane_id = plane_id;
	if (xioctl(fd, DRM_IOCTL_MODE_GETPLANE, plane) < 0)
		return -1;
	if (plane->count_format_types > max_fmts)
		plane->count_format_types = max_fmts;
	plane->format_type_ptr = (uint64_t)(uintptr_t)fmts;
	return xioctl(fd, DRM_IOCTL_MODE_GETPLANE, plane);
}

static int drm_set_plane(int fd, uint32_t plane_id, uint32_t crtc_id,
			 uint32_t fb_id, int32_t crtc_x, int32_t crtc_y,
			 uint32_t crtc_w, uint32_t crtc_h,
			 uint32_t src_x, uint32_t src_y,
			 uint32_t src_w, uint32_t src_h)
{
	struct drm_mode_set_plane sp;
	memset(&sp, 0, sizeof(sp));
	sp.plane_id = plane_id;
	sp.crtc_id = crtc_id;
	sp.fb_id = fb_id;
	sp.flags = 0;
	sp.crtc_x = crtc_x;
	sp.crtc_y = crtc_y;
	sp.crtc_w = crtc_w;
	sp.crtc_h = crtc_h;
	sp.src_x = src_x << 16;
	sp.src_y = src_y << 16;
	sp.src_w = src_w << 16;
	sp.src_h = src_h << 16;
	return xioctl(fd, DRM_IOCTL_MODE_SETPLANE, &sp);
}

static void drm_rm_fb(int fd, uint32_t fb_id)
{
	xioctl(fd, DRM_IOCTL_MODE_RMFB, &fb_id);
}

/* ---- Per-buffer state ---- */

struct buf_info {
	uint32_t gem_handle;
	uint32_t fb_id;
	int      dmabuf_fd;
	uint32_t pitch;
	uint64_t size;
};

static int buf_create(int drm_fd, uint32_t width, uint32_t height,
		      struct buf_info *buf)
{
	memset(buf, 0, sizeof(*buf));
	buf->dmabuf_fd = -1;

	/* Allocate dumb buffer: NV12 = height*1.5 rows at 8bpp */
	struct drm_mode_create_dumb cd = {0};
	cd.width = width;
	cd.height = height * 3 / 2;
	cd.bpp = 8;
	if (xioctl(drm_fd, DRM_IOCTL_MODE_CREATE_DUMB, &cd) < 0)
		return -1;
	buf->gem_handle = cd.handle;
	buf->pitch = cd.pitch;
	buf->size = cd.size;

	/* Export as dmabuf for V4L2 import */
	struct drm_prime_handle exp = {0};
	exp.handle = cd.handle;
	exp.flags = O_RDWR;
	if (xioctl(drm_fd, DRM_IOCTL_PRIME_HANDLE_TO_FD, &exp) < 0)
		goto err;
	buf->dmabuf_fd = exp.fd;

	/* Create NV12 framebuffer for scanout */
	struct drm_mode_fb_cmd2 fb = {0};
	fb.width = width;
	fb.height = height;
	fb.pixel_format = DRM_FORMAT_NV12;
	fb.handles[0] = cd.handle;
	fb.pitches[0] = cd.pitch;
	fb.offsets[0] = 0;
	fb.handles[1] = cd.handle;
	fb.pitches[1] = cd.pitch;
	fb.offsets[1] = cd.pitch * height;
	if (xioctl(drm_fd, DRM_IOCTL_MODE_ADDFB2, &fb) < 0)
		goto err;
	buf->fb_id = fb.fb_id;

	return 0;
err:
	if (buf->dmabuf_fd >= 0) close(buf->dmabuf_fd);
	{
		struct drm_mode_destroy_dumb dd = { .handle = cd.handle };
		xioctl(drm_fd, DRM_IOCTL_MODE_DESTROY_DUMB, &dd);
	}
	return -1;
}

static void buf_destroy(int drm_fd, struct buf_info *buf)
{
	if (buf->fb_id)
		drm_rm_fb(drm_fd, buf->fb_id);
	if (buf->dmabuf_fd >= 0)
		close(buf->dmabuf_fd);
	if (buf->gem_handle) {
		struct drm_mode_destroy_dumb dd = { .handle = buf->gem_handle };
		xioctl(drm_fd, DRM_IOCTL_MODE_DESTROY_DUMB, &dd);
	}
}

/* ---- Plane finder ---- */

/*
 * Collect up to max_want NV12-capable planes usable by this CRTC. Idle planes
 * (no active fb) are preferred and listed first. Returns the number found.
 */
static uint32_t find_nv12_planes(int drm_fd, uint32_t crtc_index,
				 uint32_t *out, uint32_t max_want)
{
	uint32_t plane_ids[64];
	uint32_t count = 0;
	uint32_t n_idle = 0, n_busy = 0;
	uint32_t busy[64];

	if (drm_get_plane_resources(drm_fd, plane_ids, 64, &count) < 0)
		return 0;

	for (uint32_t i = 0; i < count && n_idle < max_want; i++) {
		struct drm_mode_get_plane plane;
		uint32_t fmts[64];

		if (drm_get_plane(drm_fd, plane_ids[i], &plane, fmts, 64) < 0)
			continue;
		if (!(plane.possible_crtcs & (1u << crtc_index)))
			continue;

		int has_nv12 = 0;
		for (uint32_t j = 0; j < plane.count_format_types; j++) {
			if (fmts[j] == DRM_FORMAT_NV12) {
				has_nv12 = 1;
				break;
			}
		}
		if (!has_nv12)
			continue;

		if (plane.fb_id == 0)
			out[n_idle++] = plane_ids[i];
		else if (n_busy < 64)
			busy[n_busy++] = plane_ids[i];
	}

	/* Backfill with busy-but-compatible planes if we still need more. */
	for (uint32_t i = 0; i < n_busy && n_idle < max_want; i++)
		out[n_idle++] = busy[i];

	return n_idle;
}

static int find_crtc_index(uint32_t *crtc_ids, uint32_t count, uint32_t crtc_id)
{
	for (uint32_t i = 0; i < count; i++) {
		if (crtc_ids[i] == crtc_id)
			return (int)i;
	}
	return -1;
}

/* ---- Per-camera state ---- */

struct cam {
	const char     *dev;
	int             vfd;
	uint32_t        width, height;
	struct buf_info bufs[NUM_BUFS];
	uint32_t        num_bufs;
	uint32_t        plane_id;
	/* fitted destination rect within the display */
	int32_t         dst_x, dst_y;
	uint32_t        dst_w, dst_h;
	int             streaming;
};

/* Fit a wxh source into a tile rect, preserving aspect, centered. */
static void fit_rect(uint32_t sw, uint32_t sh,
		     int32_t tile_x, int32_t tile_y,
		     uint32_t tile_w, uint32_t tile_h,
		     int32_t *dx, int32_t *dy, uint32_t *dw, uint32_t *dh)
{
	uint32_t w, h;
	if ((uint64_t)sw * tile_h > (uint64_t)sh * tile_w) {
		w = tile_w;
		h = (uint32_t)((uint64_t)sh * tile_w / sw);
	} else {
		h = tile_h;
		w = (uint32_t)((uint64_t)sw * tile_h / sh);
	}
	*dw = w;
	*dh = h;
	*dx = tile_x + (int32_t)(tile_w - w) / 2;
	*dy = tile_y + (int32_t)(tile_h - h) / 2;
}

static int cam_start(struct cam *c, int drm_fd)
{
	c->vfd = open(c->dev, O_RDWR);
	if (c->vfd < 0) {
		fprintf(stderr, "open(%s): %s\n", c->dev, strerror(errno));
		return -1;
	}

	struct v4l2_format fmt;
	memset(&fmt, 0, sizeof(fmt));
	fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
	if (xioctl(c->vfd, VIDIOC_G_FMT, &fmt) < 0) {
		fprintf(stderr, "%s G_FMT: %s\n", c->dev, strerror(errno));
		return -1;
	}
	c->width = fmt.fmt.pix.width;
	c->height = fmt.fmt.pix.height;
	printf("%s: %ux%u %.4s\n", c->dev, c->width, c->height,
	       (char *)&fmt.fmt.pix.pixelformat);

	for (uint32_t i = 0; i < NUM_BUFS; i++) {
		if (buf_create(drm_fd, c->width, c->height, &c->bufs[i]) < 0) {
			fprintf(stderr, "%s buf_create[%u]: %s\n",
				c->dev, i, strerror(errno));
			return -1;
		}
	}

	struct v4l2_requestbuffers reqbufs;
	memset(&reqbufs, 0, sizeof(reqbufs));
	reqbufs.count = NUM_BUFS;
	reqbufs.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
	reqbufs.memory = V4L2_MEMORY_DMABUF;
	if (xioctl(c->vfd, VIDIOC_REQBUFS, &reqbufs) < 0) {
		fprintf(stderr, "%s REQBUFS: %s\n", c->dev, strerror(errno));
		return -1;
	}
	c->num_bufs = reqbufs.count < NUM_BUFS ? reqbufs.count : NUM_BUFS;

	for (uint32_t i = 0; i < c->num_bufs; i++) {
		struct v4l2_buffer vbuf;
		memset(&vbuf, 0, sizeof(vbuf));
		vbuf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
		vbuf.memory = V4L2_MEMORY_DMABUF;
		vbuf.index = i;
		vbuf.m.fd = c->bufs[i].dmabuf_fd;
		if (xioctl(c->vfd, VIDIOC_QBUF, &vbuf) < 0) {
			fprintf(stderr, "%s QBUF[%u]: %s\n",
				c->dev, i, strerror(errno));
			return -1;
		}
	}

	enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
	if (xioctl(c->vfd, VIDIOC_STREAMON, &type) < 0) {
		fprintf(stderr, "%s STREAMON: %s\n", c->dev, strerror(errno));
		return -1;
	}
	c->streaming = 1;
	return 0;
}

static void cam_stop(struct cam *c, int drm_fd)
{
	if (c->streaming) {
		enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
		xioctl(c->vfd, VIDIOC_STREAMOFF, &type);
		c->streaming = 0;
	}
	if (c->vfd >= 0) {
		struct v4l2_requestbuffers reqbufs;
		memset(&reqbufs, 0, sizeof(reqbufs));
		reqbufs.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
		reqbufs.memory = V4L2_MEMORY_DMABUF;
		reqbufs.count = 0;
		xioctl(c->vfd, VIDIOC_REQBUFS, &reqbufs);
	}
	for (uint32_t i = 0; i < NUM_BUFS; i++)
		buf_destroy(drm_fd, &c->bufs[i]);
	if (c->vfd >= 0)
		close(c->vfd);
	c->vfd = -1;
}

/* ---- Main ---- */

int main(int argc, char **argv)
{
	if (argc < 4) {
		fprintf(stderr,
			"Usage: %s <card> <config> <dev0> [dev1 ...]\n", argv[0]);
		return 2;
	}

	const char *drm_dev = argv[1];
	/* argv[2] = config (informational); layout follows the device count. */
	int ncam = argc - 3;
	if (ncam > MAX_CAMS)
		ncam = MAX_CAMS;

	struct cam cams[MAX_CAMS];
	memset(cams, 0, sizeof(cams));
	for (int i = 0; i < ncam; i++) {
		cams[i].dev = argv[3 + i];
		cams[i].vfd = -1;
	}

	signal(SIGINT, sighandler);
	signal(SIGTERM, sighandler);

	int ret = 1;

	/* ---- DRM setup ---- */
	int dfd = open(drm_dev, O_RDWR);
	if (dfd < 0) {
		fprintf(stderr, "open(%s): %s\n", drm_dev, strerror(errno));
		return 1;
	}
	printf("DRM: opened %s\n", drm_dev);

	drm_set_client_cap(dfd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1);

	uint32_t conn_ids[16], crtc_ids[16];
	struct drm_mode_card_res res;
	if (drm_get_resources(dfd, &res, conn_ids, crtc_ids, 16, 16) < 0) {
		fprintf(stderr, "GetResources: %s\n", strerror(errno));
		goto out_drm;
	}

	uint32_t chosen_crtc_id = 0;
	struct drm_mode_modeinfo chosen_mode;
	memset(&chosen_mode, 0, sizeof(chosen_mode));

	for (uint32_t i = 0; i < res.count_connectors; i++) {
		struct drm_mode_get_connector conn;
		struct drm_mode_modeinfo modes[64];
		if (drm_get_connector(dfd, conn_ids[i], &conn, modes, 64) < 0)
			continue;
		if (conn.connection != 1 || conn.count_modes == 0 ||
		    conn.encoder_id == 0)
			continue;
		struct drm_mode_get_encoder enc;
		if (drm_get_encoder(dfd, conn.encoder_id, &enc) < 0 ||
		    enc.crtc_id == 0)
			continue;
		chosen_crtc_id = enc.crtc_id;
		struct drm_mode_crtc crtc;
		if (drm_get_crtc(dfd, chosen_crtc_id, &crtc) == 0 &&
		    crtc.mode_valid)
			chosen_mode = crtc.mode;
		else
			chosen_mode = modes[0];
		printf("DRM: CRTC %u, mode %s %ux%u@%u\n",
		       chosen_crtc_id, chosen_mode.name,
		       chosen_mode.hdisplay, chosen_mode.vdisplay,
		       chosen_mode.vrefresh);
		break;
	}
	if (!chosen_crtc_id) {
		fprintf(stderr, "No connected display\n");
		goto out_drm;
	}

	int crtc_idx = find_crtc_index(crtc_ids, res.count_crtcs, chosen_crtc_id);
	if (crtc_idx < 0)
		goto out_drm;

	/* ---- Plane discovery ---- */
	uint32_t planes[MAX_CAMS];
	uint32_t nplanes = find_nv12_planes(dfd, (uint32_t)crtc_idx,
					    planes, (uint32_t)ncam);
	printf("DRM: %u NV12 plane(s) available for %d camera(s)\n",
	       nplanes, ncam);
	if (nplanes == 0) {
		fprintf(stderr, "No NV12 planes on this CRTC\n");
		goto out_drm;
	}
	if (nplanes < (uint32_t)ncam) {
		fprintf(stderr,
			"WARNING: only %u plane(s) for %d camera(s); "
			"displaying first %u channel(s)\n",
			nplanes, ncam, nplanes);
		ncam = (int)nplanes;
	}

	/* ---- Tiling geometry (matches mmap: cols=ceil(sqrt(n))) ---- */
	uint32_t disp_w = chosen_mode.hdisplay;
	uint32_t disp_h = chosen_mode.vdisplay;
	int cols = (int)ceil(sqrt((double)ncam));
	int rows = (cols > 0) ? (ncam + cols - 1) / cols : 1;
	uint32_t tile_w = disp_w / (uint32_t)cols;
	uint32_t tile_h = disp_h / (uint32_t)rows;
	printf("Layout: %d tile(s) in %dx%d grid, tile %ux%u\n",
	       ncam, cols, rows, tile_w, tile_h);

	/* ---- Bring up each camera ---- */
	int started = 0;
	for (int i = 0; i < ncam; i++) {
		if (cam_start(&cams[i], dfd) < 0) {
			fprintf(stderr, "%s: failed to start\n", cams[i].dev);
			goto out_cams;
		}
		cams[i].plane_id = planes[i];
		int col = i % cols;
		int row = i / cols;
		fit_rect(cams[i].width, cams[i].height,
			 (int32_t)(col * tile_w), (int32_t)(row * tile_h),
			 tile_w, tile_h,
			 &cams[i].dst_x, &cams[i].dst_y,
			 &cams[i].dst_w, &cams[i].dst_h);
		printf("%s -> plane %u tile(%d,%d) %ux%u at (%d,%d)\n",
		       cams[i].dev, cams[i].plane_id, col, row,
		       cams[i].dst_w, cams[i].dst_h,
		       cams[i].dst_x, cams[i].dst_y);
		started++;
	}

	printf("Streaming %d channel(s). Ctrl+C to stop\n\n", started);

	/* ---- Main loop: poll all cameras, scan out newest frame per plane ---- */
	struct pollfd pfds[MAX_CAMS];
	uint32_t frames = 0;
	while (running) {
		for (int i = 0; i < started; i++) {
			pfds[i].fd = cams[i].vfd;
			pfds[i].events = POLLIN;
			pfds[i].revents = 0;
		}
		int pr = poll(pfds, started, 1000);
		if (pr < 0) {
			if (errno == EINTR)
				continue;
			fprintf(stderr, "poll: %s\n", strerror(errno));
			break;
		}
		if (pr == 0)
			continue;

		for (int i = 0; i < started; i++) {
			if (!(pfds[i].revents & POLLIN))
				continue;

			struct v4l2_buffer vbuf;
			memset(&vbuf, 0, sizeof(vbuf));
			vbuf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
			vbuf.memory = V4L2_MEMORY_DMABUF;
			if (xioctl(cams[i].vfd, VIDIOC_DQBUF, &vbuf) < 0) {
				if (errno == EINTR || errno == EAGAIN)
					continue;
				fprintf(stderr, "%s DQBUF: %s\n",
					cams[i].dev, strerror(errno));
				running = 0;
				break;
			}

			uint32_t idx = vbuf.index;
			if (drm_set_plane(dfd, cams[i].plane_id, chosen_crtc_id,
					  cams[i].bufs[idx].fb_id,
					  cams[i].dst_x, cams[i].dst_y,
					  cams[i].dst_w, cams[i].dst_h,
					  0, 0, cams[i].width, cams[i].height) < 0) {
				if (frames == 0)
					fprintf(stderr, "%s SetPlane: %s\n",
						cams[i].dev, strerror(errno));
			}

			vbuf.m.fd = cams[i].bufs[idx].dmabuf_fd;
			if (xioctl(cams[i].vfd, VIDIOC_QBUF, &vbuf) < 0) {
				fprintf(stderr, "%s QBUF: %s\n",
					cams[i].dev, strerror(errno));
				running = 0;
				break;
			}

			frames++;
			if ((frames % 60) == 0) {
				printf("\rFrames: %u", frames);
				fflush(stdout);
			}
		}
	}

	printf("\n%u frames\n", frames);
	ret = 0;

	/* Detach planes before tearing down buffers. */
	for (int i = 0; i < started; i++)
		drm_set_plane(dfd, cams[i].plane_id, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);

out_cams:
	for (int i = 0; i < ncam; i++)
		cam_stop(&cams[i], dfd);

out_drm:
	close(dfd);
	return ret;
}
