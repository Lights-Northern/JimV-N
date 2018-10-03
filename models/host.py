#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import time
import traceback
import Queue
import libvirt
import json
import subprocess
import jimit as ji
import xml.etree.ElementTree as ET

import psutil
import cpuinfo
import dmidecode
import threading

from initialize import config, logger, r, log_emit, response_emit, host_event_emit, guest_collection_performance_emit, \
    threads_status, host_collection_performance_emit, guest_event_emit, q_creating_guest
from guest import Guest
from storage import Storage
from utils import Utils, QGA


__author__ = 'James Iter'
__date__ = '2017/3/1'
__contact__ = 'james.iter.cn@gmail.com'
__copyright__ = '(c) 2017 by James Iter.'


class Host(object):
    def __init__(self):
        self.conn = None
        self.dom = None
        self.dom_mapping_by_uuid = dict()
        self.hostname = ji.Common.get_hostname()
        # 根据 hostname 生成的 node_id
        self.node_id = Utils.get_node_id()
        self.cpu = psutil.cpu_count()
        self.cpuinfo = cpuinfo.get_cpu_info()
        self.memory = psutil.virtual_memory().total
        # 返回 json 格式数据
        self.dmidecode = dmidecode.QuerySection('all')
        self.interfaces = dict()
        self.disks = dict()
        # host, guest 性能收集统计周期，单位(秒)
        self.interval = 60
        self.last_host_traffic = dict()
        self.last_host_disk_io = dict()
        self.last_guest_cpu_time = dict()
        self.last_guest_traffic = dict()
        self.last_guest_disk_io = dict()
        self.ts = ji.Common.ts()
        self.version = config['version']

        self.init_conn()

    def init_conn(self):
        if self.conn is None:
            try:
                self.conn = libvirt.open()
            except Exception as e:
                logger.error(e.message)

    def refresh_dom_mapping(self):
        # 调用该方法的函数，都为单独的对象实例。即不存在多线程共用该方法，故而不用加多线程锁
        self.dom_mapping_by_uuid.clear()
        try:
            for dom in self.conn.listAllDomains():
                self.dom_mapping_by_uuid[dom.UUIDString()] = dom
        except libvirt.libvirtError as e:
            # 尝试重连 Libvirtd
            logger.warn(e.message)
            logger.warn(libvirt.virGetLastErrorMessage())
            self.init_conn()

    # 使用时，创建独立的实例来避开 多线程 的问题
    def instruction_process_engine(self):

        ps = r.pubsub(ignore_subscribe_messages=False)
        ps.subscribe(config['instruction_channel'])

        while True:
            if Utils.exit_flag:
                msg = 'Thread instruction_process_engine say bye-bye'
                print msg
                logger.info(msg=msg)
                return

            threads_status['instruction_process_engine'] = {'timestamp': ji.Common.ts()}

            msg = dict()
            extend_data = dict()

            try:
                msg = ps.get_message(timeout=config['engine_cycle_interval'])

                if msg is None or 'data' not in msg or not isinstance(msg['data'], basestring):
                    continue

                try:
                    msg = json.loads(msg['data'])

                    if msg['action'] == 'pong':
                        continue

                    if msg['action'] == 'ping':
                        # 通过 ping pong 来刷存在感。因为经过实际测试发现，当订阅频道长时间没有数据来往，那么订阅者会被自动退出。
                        r.publish(config['instruction_channel'], message=json.dumps({'action': 'pong'}))
                        continue

                except ValueError as e:
                    log_emit.error(e.message)
                    continue

                if 'node_id' in msg and int(msg['node_id']) != self.node_id:
                    continue

                # 下列语句繁琐写法如 <code>if '_object' not in msg or 'action' not in msg:</code>
                if not all([key in msg for key in ['_object', 'action']]):
                    continue

                logger.info(msg=msg)
                if msg['_object'] == 'guest':

                    self.refresh_dom_mapping()
                    if msg['action'] not in ['create']:
                        self.dom = self.dom_mapping_by_uuid[msg['uuid']]
                        assert isinstance(self.dom, libvirt.virDomain)

                    if msg['action'] == 'create':
                        t = threading.Thread(target=Guest.create, args=(self.conn, msg))
                        t.setDaemon(False)
                        t.start()
                        continue

                    elif msg['action'] == 'reboot':
                        Guest.reboot(dom=self.dom)

                    elif msg['action'] == 'force_reboot':
                        Guest.force_reboot(dom=self.dom, msg=msg)

                    elif msg['action'] == 'shutdown':
                        Guest.shutdown(dom=self.dom)

                    elif msg['action'] == 'force_shutdown':
                        Guest.force_shutdown(dom=self.dom)

                    elif msg['action'] == 'boot':
                        Guest.boot(dom=self.dom, msg=msg)

                    elif msg['action'] == 'suspend':
                        Guest.suspend(dom=self.dom)

                    elif msg['action'] == 'resume':
                        Guest.resume(dom=self.dom)

                    elif msg['action'] == 'delete':
                        Guest.delete(dom=self.dom, msg=msg)

                    elif msg['action'] == 'reset_password':
                        Guest.reset_password(dom=self.dom, msg=msg)

                    elif msg['action'] == 'attach_disk':
                        Guest.attach_disk(dom=self.dom, msg=msg)

                    elif msg['action'] == 'detach_disk':
                        Guest.detach_disk(dom=self.dom, msg=msg)

                    elif msg['action'] == 'update_ssh_key':
                        Guest.update_ssh_key(dom=self.dom, msg=msg)

                    elif msg['action'] == 'allocate_bandwidth':
                        t = threading.Thread(target=Guest.allocate_bandwidth, args=(self.dom, msg))
                        t.setDaemon(False)
                        t.start()
                        continue

                    elif msg['action'] == 'adjust_ability':
                        t = threading.Thread(target=Guest.adjust_ability, args=(self.dom, msg))
                        t.setDaemon(False)
                        t.start()
                        continue

                    elif msg['action'] == 'migrate':
                        Guest().migrate(dom=self.dom, msg=msg)

                elif msg['_object'] == 'disk':

                    if msg['action'] == 'create':
                        Storage(storage_mode=msg['storage_mode'], dfs_volume=msg['dfs_volume']).make_image(
                            path=msg['image_path'], size=msg['size'])

                    elif msg['action'] == 'delete':
                        Storage(storage_mode=msg['storage_mode'], dfs_volume=msg['dfs_volume']).delete_image(
                            path=msg['image_path'])

                    elif msg['action'] == 'resize':
                        mounted = True if msg['guest_uuid'].__len__() == 36 else False

                        if mounted:
                            self.refresh_dom_mapping()
                            self.dom = self.dom_mapping_by_uuid[msg['guest_uuid']]

                        # 在线磁盘扩容
                        if mounted and self.dom.isActive():
                            # 磁盘大小默认单位为KB，乘以两个 1024，使其单位达到 GiB
                            msg['size'] = int(msg['size']) * 1024 * 1024

                            # https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainBlockResize
                            self.dom.blockResize(disk=msg['device_node'], size=msg['size'])
                            Guest.quota(dom=self.dom, msg=msg)

                        # 离线磁盘扩容
                        else:
                            Storage(storage_mode=msg['storage_mode'], dfs_volume=msg['dfs_volume']).resize_image(
                                path=msg['image_path'], size=msg['size'])

                    elif msg['action'] == 'quota':
                        self.refresh_dom_mapping()
                        self.dom = self.dom_mapping_by_uuid[msg['guest_uuid']]
                        Guest.quota(dom=self.dom, msg=msg)

                elif msg['_object'] == 'snapshot':

                    self.refresh_dom_mapping()
                    self.dom = self.dom_mapping_by_uuid[msg['uuid']]

                    if msg['action'] == 'create':
                        t = threading.Thread(target=Guest.create_snapshot, args=(self.dom, msg))
                        t.setDaemon(False)
                        t.start()
                        continue

                    elif msg['action'] == 'delete':
                        t = threading.Thread(target=Guest.delete_snapshot, args=(self.dom, msg))
                        t.setDaemon(False)
                        t.start()
                        continue

                    elif msg['action'] == 'revert':
                        t = threading.Thread(target=Guest.revert_snapshot, args=(self.dom, msg))
                        t.setDaemon(False)
                        t.start()
                        continue

                    elif msg['action'] == 'convert':
                        t = threading.Thread(target=Guest.convert_snapshot, args=(msg,))
                        t.setDaemon(False)
                        t.start()
                        continue

                elif msg['_object'] == 'os_template_image':
                    if msg['action'] == 'delete':
                        Storage(storage_mode=msg['storage_mode'], dfs_volume=msg['dfs_volume']).delete_image(
                            path=msg['template_path'])

                elif msg['_object'] == 'global':
                    if msg['action'] == 'refresh_guest_state':
                        t = threading.Thread(target=Host().refresh_guest_state, args=())
                        t.setDaemon(False)
                        t.start()
                        continue

                    if msg['action'] == 'upgrade':
                        try:
                            log = self.upgrade(msg['url'])
                            log_emit.info(msg=log)

                        except subprocess.CalledProcessError as e:
                            log_emit.warn(e.output)
                            self.rollback()
                            continue

                        log = self.restart()
                        log_emit.info(msg=log)

                    if msg['action'] == 'restart':
                        log = self.restart()
                        log_emit.info(msg=log)

                else:
                    err = u'未支持的 _object：' + msg['_object']
                    log_emit.error(err)

                response_emit.success(_object=msg['_object'], action=msg['action'], uuid=msg['uuid'],
                                      data=extend_data, passback_parameters=msg.get('passback_parameters'))

            except KeyError as e:
                log_emit.warn(e.message)
                if msg['_object'] == 'guest':
                    if msg['action'] == 'delete':
                        response_emit.success(_object=msg['_object'], action=msg['action'], uuid=msg['uuid'],
                                              data=extend_data, passback_parameters=msg.get('passback_parameters'))

            except:
                # 防止循环线程，在redis连接断开时，混水写入日志
                time.sleep(5)
                log_emit.error(traceback.format_exc())
                response_emit.failure(_object=msg['_object'], action=msg.get('action'), uuid=msg.get('uuid'),
                                      passback_parameters=msg.get('passback_parameters'))

    @staticmethod
    def guest_creating_progress_report_engine():
        """
        Guest 创建进度上报引擎
        """

        list_creating_guest = list()
        template_size = dict()

        while True:
            if Utils.exit_flag:
                msg = 'Thread guest_creating_progress_report_engine say bye-bye'
                print msg
                logger.info(msg=msg)
                return

            try:
                try:
                    payload = q_creating_guest.get(timeout=config['engine_cycle_interval'])
                    list_creating_guest.append(payload)
                    q_creating_guest.task_done()
                except Queue.Empty as e:
                    pass

                # 当有 Guest 被创建时，略微等待一下，避免复制模板的动作还没开始，就开始计算进度。这样会产生找不到镜像路径的异常。
                time.sleep(1)

                threads_status['guest_creating_progress_report_engine'] = {'timestamp': ji.Common.ts()}

                for i, guest in enumerate(list_creating_guest):

                    template_path = guest['template_path']

                    storage = Storage(storage_mode=guest['storage_mode'], dfs_volume=guest['dfs_volume'])

                    if template_path not in template_size:
                        template_size[template_path] = float(storage.getsize(path=template_path))

                    system_image_size = storage.getsize(path=guest['system_image_path'])
                    progress = system_image_size / template_size[template_path]

                    guest_event_emit.creating(uuid=guest['uuid'], progress=int(progress * 90))

                    if progress >= 1:
                        del list_creating_guest[i]

            except:
                log_emit.warn(traceback.format_exc())

    def update_interfaces(self):
        self.interfaces.clear()
        for nic_name, nic_s in psutil.net_if_addrs().items():
            for nic in nic_s:
                # 参考链接：https://github.com/torvalds/linux/blob/5518b69b76680a4f2df96b1deca260059db0c2de/include/linux/socket.h
                if nic.family == 2:
                    for _nic in nic_s:
                        if _nic.family == 2:
                            self.interfaces[nic_name] = {'ip': _nic.address, 'netmask': _nic.netmask}

                        if _nic.family == 17:
                            self.interfaces[nic_name]['mac'] = _nic.address

    def update_disks(self):
        self.disks.clear()
        for disk in psutil.disk_partitions(all=False):
            disk_usage = psutil.disk_usage(disk.mountpoint)
            self.disks[disk.mountpoint] = {'device': disk.device, 'real_device': disk.device, 'fstype': disk.fstype,
                                           'opts': disk.opts, 'total': disk_usage.total, 'used': disk_usage.used,
                                           'free': disk_usage.free, 'percent': disk_usage.percent}

            if os.path.islink(disk.device):
                self.disks[disk.mountpoint]['real_device'] = os.path.realpath(disk.device)

    # 使用时，创建独立的实例来避开 多线程 的问题
    def guest_state_report_engine(self):
        """
        Guest 状态上报引擎
        """
        guest_state_mapping = dict()

        while True:
            if Utils.exit_flag:
                msg = 'Thread guest_state_report_engine say bye-bye'
                print msg
                logger.info(msg=msg)
                return

            try:
                # 3 秒钟更新一次
                time.sleep(config['engine_cycle_interval'] * 3)
                threads_status['guest_state_report_engine'] = {'timestamp': ji.Common.ts()}
                self.refresh_dom_mapping()

                for uuid, dom in self.dom_mapping_by_uuid.items():
                    state = Guest.get_state(dom=dom)

                    if uuid in guest_state_mapping and guest_state_mapping[uuid] == state:
                        continue

                    guest_state_mapping[uuid] = state
                    Guest.guest_state_report(dom=dom)

            except:
                log_emit.warn(traceback.format_exc())

    # 使用时，创建独立的实例来避开 多线程 的问题
    def host_state_report_engine(self):
        """
        计算节点状态上报引擎
        """

        # 首次启动时，做数据初始化
        self.update_interfaces()
        self.update_disks()
        boot_time = ji.Common.ts()

        while True:
            if Utils.exit_flag:
                msg = 'Thread host_state_report_engine say bye-bye'
                print msg
                logger.info(msg=msg)
                return

            try:
                time.sleep(config['engine_cycle_interval'])

                threads_status['host_state_report_engine'] = {'timestamp': ji.Common.ts()}

                # 一分钟做一次更新
                if ji.Common.ts() % 60 == 0:
                    self.update_interfaces()
                    self.update_disks()

                host_event_emit.heartbeat(message={'node_id': self.node_id, 'cpu': self.cpu, 'cpuinfo': self.cpuinfo,
                                                   'memory': self.memory, 'dmidecode': self.dmidecode,
                                                   'interfaces': self.interfaces, 'disks': self.disks,
                                                   'system_load': os.getloadavg(), 'boot_time': boot_time,
                                                   'memory_available': psutil.virtual_memory().available,
                                                   'threads_status': threads_status, 'version': self.version})

            except:
                log_emit.warn(traceback.format_exc())

    def refresh_guest_state(self):
        try:
            self.refresh_dom_mapping()

            for dom in self.dom_mapping_by_uuid.values():
                Guest.guest_state_report(dom=dom)

        except:
            log_emit.warn(traceback.format_exc())

    def guest_cpu_memory_performance_report(self):

        data = list()

        for _uuid, dom in self.dom_mapping_by_uuid.items():

            if not dom.isActive():
                continue

            _, _, _, cpu_count, _ = dom.info()
            cpu_time2 = dom.getCPUStats(True)[0]['cpu_time']

            cpu_memory = dict()

            if _uuid in self.last_guest_cpu_time:
                cpu_load = (cpu_time2 - self.last_guest_cpu_time[_uuid]['cpu_time']) / self.interval / 1000 ** 3. \
                           * 100 / cpu_count
                # 计算 cpu_load 的公式：
                # (cpu_time2 - cpu_time1) / interval_N / 1000**3.(nanoseconds to seconds) * 100(percent) /
                # cpu_count
                # cpu_time == user_time + system_time + guest_time
                #
                # 参考链接：
                # https://libvirt.org/html/libvirt-libvirt-domain.html#VIR_DOMAIN_STATS_CPU_TOTAL
                # https://stackoverflow.com/questions/40468370/what-does-cpu-time-represent-exactly-in-libvirt

                memory_info = QGA.get_guest_memory_info(dom=dom)

                memory_total = dom.maxMemory()
                memory_available = 0
                memory_rate = 0

                if memory_info.__len__() > 0:
                    memory_available = memory_info.get('MemAvailable', None)

                    if memory_available is not None:
                        memory_available = memory_available.get('value', 0)
                        memory_rate = int((1 - float(memory_available) / memory_total) * 100)

                cpu_memory = {
                    'guest_uuid': _uuid,
                    'cpu_load': cpu_load if cpu_load <= 100 else 100,
                    'memory_available': memory_available,
                    'memory_rate': memory_rate
                }

            else:
                self.last_guest_cpu_time[_uuid] = dict()

            self.last_guest_cpu_time[_uuid]['cpu_time'] = cpu_time2
            self.last_guest_cpu_time[_uuid]['timestamp'] = self.ts

            if cpu_memory.__len__() > 0:
                data.append(cpu_memory)

        if data.__len__() > 0:
            guest_collection_performance_emit.cpu_memory(data=data)

    def guest_traffic_performance_report(self):

        data = list()

        for _uuid, dom in self.dom_mapping_by_uuid.items():

            if not dom.isActive():
                continue

            root = ET.fromstring(dom.XMLDesc())

            for interface in root.findall('devices/interface'):
                dev = interface.find('target').get('dev')
                name = interface.find('alias').get('name')
                interface_state = dom.interfaceStats(dev)

                interface_id = '_'.join([_uuid, dev])

                traffic = dict()

                if interface_id in self.last_guest_traffic:

                    traffic = {
                        'guest_uuid': _uuid,
                        'name': name,
                        'rx_bytes':
                            (interface_state[0] - self.last_guest_traffic[interface_id]['rx_bytes']) / self.interval,
                        'rx_packets':
                            (interface_state[1] - self.last_guest_traffic[interface_id]['rx_packets']) / self.interval,
                        'rx_errs': interface_state[2],
                        'rx_drop': interface_state[3],
                        'tx_bytes':
                            (interface_state[4] - self.last_guest_traffic[interface_id]['tx_bytes']) / self.interval,
                        'tx_packets':
                            (interface_state[5] - self.last_guest_traffic[interface_id]['tx_packets']) / self.interval,
                        'tx_errs': interface_state[6],
                        'tx_drop': interface_state[7]
                    }

                else:
                    self.last_guest_traffic[interface_id] = dict()

                self.last_guest_traffic[interface_id]['rx_bytes'] = interface_state[0]
                self.last_guest_traffic[interface_id]['rx_packets'] = interface_state[1]
                self.last_guest_traffic[interface_id]['tx_bytes'] = interface_state[4]
                self.last_guest_traffic[interface_id]['tx_packets'] = interface_state[5]
                self.last_guest_traffic[interface_id]['timestamp'] = self.ts

                if traffic.__len__() > 0:
                    data.append(traffic)

        if data.__len__() > 0:
            guest_collection_performance_emit.traffic(data=data)

    def guest_disk_io_performance_report(self):

        data = list()

        for _uuid, dom in self.dom_mapping_by_uuid.items():

            if not dom.isActive():
                continue

            root = ET.fromstring(dom.XMLDesc())

            for disk in root.findall('devices/disk'):
                dev = disk.find('target').get('dev')
                protocol = disk.find('source').get('protocol')

                dev_path = None

                if protocol in [None, 'file']:
                    dev_path = disk.find('source').get('file')

                elif protocol == 'gluster':
                    dev_path = disk.find('source').get('name')

                if dev_path is None:
                    continue

                disk_uuid = dev_path.split('/')[-1].split('.')[0]
                disk_state = dom.blockStats(dev)

                disk_io = dict()

                if disk_uuid in self.last_guest_disk_io:

                    disk_io = {
                        'disk_uuid': disk_uuid,
                        'rd_req': (disk_state[0] - self.last_guest_disk_io[disk_uuid]['rd_req']) / self.interval,
                        'rd_bytes': (disk_state[1] - self.last_guest_disk_io[disk_uuid]['rd_bytes']) / self.interval,
                        'wr_req': (disk_state[2] - self.last_guest_disk_io[disk_uuid]['wr_req']) / self.interval,
                        'wr_bytes': (disk_state[3] - self.last_guest_disk_io[disk_uuid]['wr_bytes']) / self.interval
                    }

                else:
                    self.last_guest_disk_io[disk_uuid] = dict()

                self.last_guest_disk_io[disk_uuid]['rd_req'] = disk_state[0]
                self.last_guest_disk_io[disk_uuid]['rd_bytes'] = disk_state[1]
                self.last_guest_disk_io[disk_uuid]['wr_req'] = disk_state[2]
                self.last_guest_disk_io[disk_uuid]['wr_bytes'] = disk_state[3]
                self.last_guest_disk_io[disk_uuid]['timestamp'] = self.ts

                if disk_io.__len__() > 0:
                    data.append(disk_io)

        if data.__len__() > 0:
            guest_collection_performance_emit.disk_io(data=data)

    def guest_performance_collection_engine(self):

        while True:
            if Utils.exit_flag:
                msg = 'Thread guest_performance_collection_engine say bye-bye'
                print msg
                logger.info(msg=msg)
                return

            try:
                time.sleep(config['engine_cycle_interval'])
                threads_status['guest_performance_collection_engine'] = {'timestamp': ji.Common.ts()}
                self.ts = ji.Common.ts()

                if self.ts % self.interval != 0:
                    continue

                if self.ts % 3600 == 0:
                    # 一小时做一次 垃圾回收 操作
                    for k, v in self.last_guest_cpu_time.items():
                        if (self.ts - v['timestamp']) > self.interval * 2:
                            del self.last_guest_cpu_time[k]

                    for k, v in self.last_guest_traffic.items():
                        if (self.ts - v['timestamp']) > self.interval * 2:
                            del self.last_guest_traffic[k]

                    for k, v in self.last_guest_disk_io.items():
                        if (self.ts - v['timestamp']) > self.interval * 2:
                            del self.last_guest_disk_io[k]

                self.refresh_dom_mapping()

                self.guest_cpu_memory_performance_report()
                self.guest_traffic_performance_report()
                self.guest_disk_io_performance_report()

            except:
                log_emit.warn(traceback.format_exc())

    def host_cpu_memory_performance_report(self):

        cpu_memory = {
            'node_id': self.node_id,
            'cpu_load': psutil.cpu_percent(interval=None, percpu=False),
            'memory_available': psutil.virtual_memory().available,
        }

        host_collection_performance_emit.cpu_memory(data=cpu_memory)

    def host_traffic_performance_report(self):

        data = list()
        net_io = psutil.net_io_counters(pernic=True)

        for nic_name in self.interfaces.keys():
            nic = net_io.get(nic_name, None)
            if nic is None:
                continue

            traffic = list()

            if nic_name in self.last_host_traffic:
                traffic = {
                    'node_id': self.node_id,
                    'name': nic_name,
                    'rx_bytes': (nic.bytes_recv - self.last_host_traffic[nic_name].bytes_recv) / self.interval,
                    'rx_packets':
                        (nic.packets_recv - self.last_host_traffic[nic_name].packets_recv) / self.interval,
                    'rx_errs': (nic.errin - self.last_host_traffic[nic_name].errin),
                    'rx_drop': (nic.dropin - self.last_host_traffic[nic_name].dropin),
                    'tx_bytes': (nic.bytes_sent - self.last_host_traffic[nic_name].bytes_sent) / self.interval,
                    'tx_packets':
                        (nic.packets_sent - self.last_host_traffic[nic_name].packets_sent) / self.interval,
                    'tx_errs': (nic.errout - self.last_host_traffic[nic_name].errout),
                    'tx_drop': (nic.dropout - self.last_host_traffic[nic_name].dropout)
                }
            elif not isinstance(self.last_host_disk_io, dict):
                self.last_host_traffic = dict()

            self.last_host_traffic[nic_name] = nic

            if traffic.__len__() > 0:
                data.append(traffic)

        if data.__len__() > 0:
            host_collection_performance_emit.traffic(data=data)

    def host_disk_usage_io_performance_report(self):

        data = list()
        disk_io_counters = psutil.disk_io_counters(perdisk=True)

        for mountpoint, disk in self.disks.items():
            dev = os.path.basename(disk['real_device'])
            disk_usage_io = list()
            if dev in self.last_host_disk_io:
                disk_usage_io = {
                    'node_id': self.node_id,
                    'mountpoint': mountpoint,
                    'used': psutil.disk_usage(mountpoint).used,
                    'rd_req':
                        (disk_io_counters[dev].read_count - self.last_host_disk_io[dev].read_count) / self.interval,
                    'rd_bytes':
                        (disk_io_counters[dev].read_bytes - self.last_host_disk_io[dev].read_bytes) / self.interval,
                    'wr_req':
                        (disk_io_counters[dev].write_count - self.last_host_disk_io[dev].write_count) / self.interval,
                    'wr_bytes':
                        (disk_io_counters[dev].write_bytes - self.last_host_disk_io[dev].write_bytes) / self.interval
                }

            elif not isinstance(self.last_host_disk_io, dict):
                self.last_host_disk_io = dict()

            self.last_host_disk_io[dev] = disk_io_counters[dev]

            if disk_usage_io.__len__() > 0:
                data.append(disk_usage_io)

        if data.__len__() > 0:
            host_collection_performance_emit.disk_usage_io(data=data)

    def host_performance_collection_engine(self):
        while True:
            if Utils.exit_flag:
                msg = 'Thread host_performance_collection_engine say bye-bye'
                print msg
                logger.info(msg=msg)
                return

            try:
                time.sleep(config['engine_cycle_interval'])
                threads_status['host_performance_collection_engine'] = {'timestamp': ji.Common.ts()}
                self.ts = ji.Common.ts()

                if self.ts % self.interval != 0:
                    continue

                self.update_interfaces()
                self.update_disks()
                self.host_cpu_memory_performance_report()
                self.host_traffic_performance_report()
                self.host_disk_usage_io_performance_report()

            except:
                log_emit.warn(traceback.format_exc())

    @staticmethod
    def restart():
        return subprocess.check_output(['systemctl', 'restart', 'jimvn.service'], stderr=subprocess.STDOUT)

    @staticmethod
    def upgrade(url=None):
        assert isinstance(url, basestring)

        jimvn_path = config['jimvn_path']
        backup_path = '.'.join([jimvn_path, 'bak'])

        if os.path.exists(backup_path):
            os.removedirs(backup_path)

        os.renames(jimvn_path, backup_path)

        cmd = ' '.join(['curl -sL', url, '| tar -zxf - --strip-components 1 -C', jimvn_path])

        return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)

    @staticmethod
    def rollback():
        jimvn_path = config['jimvn_path']
        backup_path = '.'.join([jimvn_path, 'bak'])

        if os.path.exists(jimvn_path):
            os.removedirs(jimvn_path)

        os.renames(backup_path, jimvn_path)

