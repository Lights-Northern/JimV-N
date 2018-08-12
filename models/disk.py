#!/usr/bin/env python
# -*- coding: utf-8 -*-


import json
import os

from utils import Utils
from jimvn_exception import CommandExecFailed

from initialize import log_emit


__author__ = 'James Iter'
__date__ = '2017/4/25'
__contact__ = 'james.iter.cn@gmail.com'
__copyright__ = '(c) 2017 by James Iter.'


class Disk(object):
    @staticmethod
    def make_qemu_image_by_glusterfs(gf, dfs_volume, image_path, size):

        if not gf.isdir(os.path.dirname(image_path)):
            gf.makedirs(os.path.dirname(image_path), 0755)

        image_path = '/'.join(['gluster://127.0.0.1', dfs_volume, image_path])

        cmd = ' '.join(['/usr/bin/qemu-img', 'create', '-f', 'qcow2', image_path, size.__str__() + 'G'])
        exit_status, output = Utils.shell_cmd(cmd)

        if exit_status != 0:
            log = u' '.join([u'路径', image_path, u'创建磁盘时，命令执行退出异常：', str(output)])
            log_emit.error(msg=log)
            raise CommandExecFailed(log)

        return True

    @staticmethod
    def make_qemu_image_by_local(image_path, size):

        if not os.path.isdir(os.path.dirname(image_path)):
            os.makedirs(os.path.dirname(image_path), 0755)

        cmd = ' '.join(['/usr/bin/qemu-img', 'create', '-f', 'qcow2', image_path, size.__str__() + 'G'])
        exit_status, output = Utils.shell_cmd(cmd)

        if exit_status != 0:
            log = u' '.join([u'路径', image_path, u'创建磁盘时，命令执行退出异常：', str(output)])
            log_emit.error(msg=log)
            raise CommandExecFailed(log)

        return True

    @staticmethod
    def resize_qemu_image_by_glusterfs(dfs_volume, image_path, size):
        image_path = '/'.join(['gluster://127.0.0.1', dfs_volume, image_path])

        cmd = ' '.join(['/usr/bin/qemu-img', 'resize', '-f', 'qcow2', image_path, size.__str__() + 'G'])
        exit_status, output = Utils.shell_cmd(cmd)

        if exit_status != 0:
            log = u' '.join([u'路径', image_path, u'磁盘扩容时，命令执行退出异常：', str(output)])
            log_emit.error(msg=log)
            raise CommandExecFailed(log)

        return True

    @staticmethod
    def resize_qemu_image_by_local(image_path, size):
        cmd = ' '.join(['/usr/bin/qemu-img', 'resize', '-f', 'qcow2', image_path, size.__str__() + 'G'])
        exit_status, output = Utils.shell_cmd(cmd)

        if exit_status != 0:
            log = u' '.join([u'路径', image_path, u'磁盘扩容时，命令执行退出异常：', str(output)])
            log_emit.error(msg=log)
            raise CommandExecFailed(log)

        return True

    @staticmethod
    def delete_qemu_image_by_glusterfs(gf, image_path):
        gf.remove(image_path)

    @staticmethod
    def delete_qemu_image_by_local(image_path):
        os.remove(image_path)

    @staticmethod
    def disk_info_by_glusterfs(dfs_volume, image_path):
        image_path = '/'.join(['gluster://127.0.0.1', dfs_volume, image_path])
        cmd = ' '.join(['/usr/bin/qemu-img', 'info', '--output=json', '-f', 'qcow2', image_path, '2>/dev/null'])
        exit_status, output = Utils.shell_cmd(cmd)

        if exit_status != 0:
            log = u' '.join([u'路径', image_path, u'磁盘扩容时，命令执行退出异常：', str(output)])
            log_emit.error(msg=log)
            raise CommandExecFailed(log)

        return json.loads(output)

    @staticmethod
    def disk_info_by_local(image_path):
        cmd = ' '.join(['/usr/bin/qemu-img', 'info', '--output=json', '-f', 'qcow2', image_path, '2>/dev/null'])
        exit_status, output = Utils.shell_cmd(cmd)

        if exit_status != 0:
            log = u' '.join([u'路径', image_path, u'磁盘扩容时，命令执行退出异常：', str(output)])
            log_emit.error(msg=log)
            raise CommandExecFailed(log)

        return json.loads(output)

