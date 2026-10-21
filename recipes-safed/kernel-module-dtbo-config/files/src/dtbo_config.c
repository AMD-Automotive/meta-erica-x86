// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Simple device tree overlay management tool
 *
 * Copyright (C) 2026, Advanced Micro Devices, Inc.
 */
#include <linux/configfs.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_fdt.h>
#include <linux/of_platform.h>
#include <linux/sizes.h>

struct dtbo {
	int id;
	struct list_head dtbo_list;
};

static LIST_HEAD(dtbo_list);

static ssize_t dtbo_pop_show(struct config_item *item, char *page)
{
	struct dtbo *dtbo;
	int ret = sprintf(page, "overlays applied: ["), back_track = 0;

	list_for_each_entry(dtbo, &dtbo_list, dtbo_list) {
		ret += sprintf(page + ret, "#%d ", dtbo->id);
		back_track = 1;
	}
	ret += sprintf(page + ret - back_track, "]\n");

	return ret - back_track;
}

static ssize_t dtbo_pop_store(struct config_item *item, const char *page,
			      size_t count)
{

	struct dtbo *dtbo;
	int input, ret;

	ret = kstrtoint(page, 10, &input);
	if (ret < 0)
		return ret;

	if (input && !list_empty(&dtbo_list)) {
		dtbo = list_last_entry(&dtbo_list, struct dtbo, dtbo_list);
		ret = of_overlay_remove(&dtbo->id);
		if (ret < 0)
			return ret;
		of_platform_populate(NULL, NULL, NULL, NULL);
		__list_del_entry(&dtbo->dtbo_list);
		kfree(dtbo);
	}

	return count;
}

static ssize_t dtbo_push_read(struct config_item *item, void *buf,
			      size_t max_count)
{
	return 0;
}

static ssize_t dtbo_push_write(struct config_item *item, const void *buf,
			       size_t count)
{
	int ret;
	struct dtbo *dtbo;

	dtbo = kzalloc(sizeof(*dtbo), GFP_KERNEL);
	if (!dtbo)
		return -ENOMEM;

	INIT_LIST_HEAD(&dtbo->dtbo_list);

	ret = of_overlay_fdt_apply(buf, count, &dtbo->id, NULL);
	if (ret < 0) {
		pr_err("failed to apply dt overlay\n");
		kfree(dtbo);
		return ret;
	}

	ret = of_platform_populate(NULL, NULL, NULL, NULL);
	if (ret < 0) {
		pr_err("failed to populate platform update\n");
		kfree(dtbo);
		return ret;
	}

	list_add_tail(&dtbo->dtbo_list, &dtbo_list);

	return count;
}

CONFIGFS_ATTR(dtbo_, pop);
CONFIGFS_BIN_ATTR(dtbo_, push, NULL, SZ_1M);

static struct configfs_attribute *dtbo_config_attrs[] = {
	&dtbo_attr_pop,
	NULL,
};

static struct configfs_bin_attribute *dtbo_bin_config_attrs[] = {
	&dtbo_attr_push,
	NULL,
};

static struct config_item_type dtbo_config_type = {
	.ct_attrs	= dtbo_config_attrs,
	.ct_bin_attrs	= dtbo_bin_config_attrs,
	.ct_owner	= THIS_MODULE,
};

static struct configfs_subsystem dtbo_config_root = {
	.su_group = {
        	.cg_item = {
			.ci_namebuf	= "dt-overlays",
			.ci_type	= &dtbo_config_type,
		},
	},
 	.su_mutex = __MUTEX_INITIALIZER(dtbo_config_root.su_mutex),
};

static int __init dtbo_config_module_init(void)
{
	config_group_init(&dtbo_config_root.su_group);

	return configfs_register_subsystem(&dtbo_config_root);
}

static void __exit dtbo_config_module_exit(void)
{
	struct dtbo *dtbo, *dtbo_n;

	of_overlay_remove_all();
	configfs_unregister_subsystem(&dtbo_config_root);
	of_platform_populate(NULL, NULL, NULL, NULL);
	list_for_each_entry_safe(dtbo, dtbo_n, &dtbo_list, dtbo_list) {
		__list_del_entry(&dtbo->dtbo_list);
		kfree(dtbo);
	}
}

module_init(dtbo_config_module_init);
module_exit(dtbo_config_module_exit);

MODULE_AUTHOR("Anatoliy Klymenko");
MODULE_DESCRIPTION("Device Tree Overlay Configuration File System Interface");
MODULE_LICENSE("GPL");