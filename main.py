#!/usr/bin/env python3
import re
import logging
import argparse
import os
import subprocess
import time
import signal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)-15s %(levelname)-8s %(name)-20s %(message)s'
)
logger = logging.getLogger('waveroam')
parser = argparse.ArgumentParser(description='waveroam')
parser.add_argument('-i', '--interface', required=True)
parser.add_argument('-r', '--roam-threshold', required=True, type=int)
parser.add_argument('-N', '--no-dhcp', action='store_const', const=True, default=False)
args = parser.parse_args()


running = False
scan_deadline = 0


def kill_pid_if_exists(pidfile):
    try:
        with open(pidfile) as fp:
            pid = int(fp.read().strip())
            logger.debug('killing pid from (%s)', pidfile)
            while True:
                try:
                    os.kill(pid, signal.SIGTERM)
                    logger.debug('wait...')
                    time.sleep(1)
                except OSError as oe:
                    if oe.errno == 3:
                        logger.debug("process exited")
                        return (True, pid)
                    raise
    except IOError as ioe:
        if ioe.errno == 2:
            return (False, 0)
        raise



def pid_running(pidfile):
    try:
        with open(pidfile) as fp:
            pid = int(fp.read().strip())
            try:
                os.kill(pid, 0)
                return (True, pid)
            except OSError as oe:
                if oe.errno == 3:
                    return (False, 0)
                raise
    except IOError as ioe:
        if ioe.errno == 2:
            return (False, 0)
        raise


def exec_cmd(args, failok=False):
    try:
        retstr = subprocess.check_output(args, stderr=subprocess.STDOUT)
        logger.debug('executed %s', ' '.join(args))
        return (0, retstr)
    except subprocess.CalledProcessError as cpe:
        if failok:
            return (cpe.returncode, cpe.output)
        logger.error(cpe)
        return (cpe.returncode, cpe.output)


def check_wpa_supplicant(interface):
    skt_path = '/var/run/wpa_supplicant/%s' % interface
    if not os.path.exists(skt_path):
        logger.info('starting wpa_supplicant...')
        cmd_args = [
            '/bin/systemctl', 'start', 'wpa_supplicant@%s' % interface
        ]
        ret, data = exec_cmd(cmd_args)
    else:
        return
    while not os.path.exists(skt_path):
        logger.info('waiting for wpa_supplicant command socket to appear at %s', skt_path)
        time.sleep(1)


def check_eventfeed(interface):
    pidfile = '/var/run/wpa_supplicant/events-%s.pid' % interface
    running, pid = pid_running(pidfile)
    if not running:
        logger.info('starting wpa_supplicant event feed...')
        cmd_args = [
            '/usr/bin/wpa_cli',
            '-i', interface,
            '-a', '/etc/wpa_supplicant/dhcpcd.action',
            '-P', pidfile,
            '-B'
        ]
        ret, data = exec_cmd(cmd_args)


def sighandler(signum, frame):
    global running
    running = False


def check_signal(interface):
    x = {}
    cmd_args = ['/usr/sbin/iw', interface, 'link']
    ret, data = exec_cmd(cmd_args)
    rssi = None
    for line in data.decode('ascii', errors='ignore').split('\n'):
        try:
            capture = re.match(r'signal: (?P<rssi>[0-9-]+) dBm', line.strip())
            if capture is not None:
                cgroup = capture.groupdict()
                rssi = float(cgroup['rssi'])
        except:
            pass
    return rssi


def invoke_scan(interface):
    cmd_args = [
        '/usr/bin/wpa_cli',
        '-i', interface,
        'scan'
    ]
    exec_cmd(cmd_args)


def check_dhcpcd(interface):
    pidfile = '/var/run/dhcpcd-%s.pid' % interface
    cmd_args = ['/sbin/dhcpcd', '-nLNK', interface]
    running, pid = pid_running(pidfile)
    if not running:
        logger.info('starting dhcpcd...')
        exec_cmd(cmd_args)


def rebind_dhcpcd(interface):
    cmd_args = ['/sbin/dhcpcd', '-n', interface]
    pidfile = '/var/run/dhcpcd-%s.pid' % interface
    running, pid = pid_running(pidfile)
    if running:
        logger.info('rebinding via dhcpcd')
        exec_cmd(cmd_args)


def main():
    global running
    global scan_deadline
    first_rebind = False
    signal.signal(signal.SIGTERM, sighandler)
    signal.signal(signal.SIGINT, sighandler)
    signal.signal(signal.SIGHUP, sighandler)
    running = True
    while running:
        check_wpa_supplicant(args.interface)
        if not args.no_dhcp:
            check_dhcpcd(args.interface)
        check_eventfeed(args.interface)
        wlan_signal = check_signal(args.interface)
        if wlan_signal is not None and not first_rebind and not args.no_dhcp:
            rebind_dhcpcd(args.interface)
            first_rebind = True
        if wlan_signal is None or wlan_signal < args.roam_threshold:
            cur_time = time.time()
            if cur_time >= scan_deadline:
                scan_deadline = cur_time + 1
                logger.info('signal %s<%s, invoking scan', wlan_signal, args.roam_threshold)
                invoke_scan(args.interface)
        time.sleep(0.1)
    logger.info('stopping...')
    kill_pid_if_exists('/var/run/wpa_supplicant/events-%s.pid' % args.interface)
    if not args.no_dhcp:
        exec_cmd(['/sbin/dhcpcd', '-k', args.interface], True)


if __name__ == "__main__":
    main()
