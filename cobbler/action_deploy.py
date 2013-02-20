"""

Copyright 2006-2013, Red Hat, Inc and Others
James Cammarata <jimi AT sngx DOT net>

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
02110-1301  USA
"""

import guestfs
import parted
import os.path
import shlex
import subprocess
import sys
import time

import pxegen
import utils

from cexceptions import *
from utils import _
import clogger

class Deploy:
    """
    """
    def __init__(self,config,verbose=False,logger=None):
        """
        Constructor
        """
        self.verbose     = verbose
        self.config      = config
        self.settings    = config.settings()
        self.api         = config.api
        self.distros     = config.distros()
        self.profiles    = config.profiles()
        self.systems     = config.systems()
        self.distros     = config.distros()
        if logger is None:
            logger       = clogger.Logger()
        self.logger      = logger
        self.pxe         = pxegen.PXEGen(self.config,self.logger)


    # TODO
    # add a timeout argument
    # add an option to skip kernel bundle
    # add a target cloud environment argument
    def run(self,system=None,profile=None,directory=None,skip_build=False):
        if not system and not profile:
            return False

        if not directory:
            directory = "/tmp"
        else:
            if not os.path.exists(directory) and not os.path.isdir(directory):
                self.logger.error("ERROR: the output directory (%s) specified is invalid")
                return False

        system = self.api.get_item("system",system)
        profile = system.get_parent()
        distro = profile.get_parent()
        while distro.TYPE_NAME != "distro":
            distro = distro.get_parent()

        self.logger.debug("deploy system  : %s" % system.name)
        self.logger.debug("deploy profile : %s" % profile.name)
        self.logger.debug("deploy distro  : %s" % distro.name)

        num_cpu = int(system.virt_cpus)
        num_ram = int(system.virt_ram)

        append_line = self.pxe.build_kernel_options(system,profile,distro,None,distro.arch,profile.kickstart)

        qemu_img_cmd = """qemu-img create -f %s %s/%s.img %sG""" % (system.virt_disk_driver,directory,system.name,system.virt_file_size)
        qemu_kvm_cmd = """qemu-kvm -M pc-0.14 -enable-kvm -name %s -boot once=n 
         -nographic -no-reboot -nodefconfig -nodefaults 
         -kernel %s 
         -initrd %s
         -append "%s console=tty0 console=ttyS0" 
         -smp %d,sockets=%d,cores=1,threads=1 -m %d -rtc base=utc
         -drive file=%s/%s.img,if=virtio,format=%s
         -serial telnet:localhost:20207,server,nowait
         -net user
        """ % (system.name,distro.kernel,distro.initrd,append_line,num_cpu,num_cpu,num_ram,directory,system.name,system.virt_disk_driver)

        for k in system.interfaces.keys():
            qemu_kvm_cmd += " -net nic,macaddr=%s,model=virtio\n" % system.interfaces[k]["mac_address"]

        self.logger.debug(qemu_img_cmd)
        self.logger.debug(qemu_kvm_cmd)

        qemu_img_cmd = shlex.split(qemu_img_cmd)
        qemu_kvm_cmd = shlex.split(qemu_kvm_cmd)

        for cmd in (["qemu-img creation",qemu_img_cmd],["qemu-kvm build",qemu_kvm_cmd]):
            self.logger.debug("Running %s" % cmd[0])
            po = subprocess.Popen(cmd[1])
            while po.poll() == None:
                time.sleep(1)

            if po.returncode != 0:
                self.logger.error("The %s command failed, bailing out" % cmd[0])
                return False

            self.logger.info("%s ok" % cmd[0])
        
        # TODO
        # use guest fs to extract the kernel
        # call euca-bundle-image to bundle the image and kernel files
        # call euca-upload-bundle to upload the image and kernel files
        # call euca-register to register the image and kernel files
        # if a system, call euca-run-instance 

        self.logger.debug("Making modifications to the generated image...")
        g = guestfs.GuestFS()
        g.add_drive_opts("%s/%s.img" % (directory,system.name), format=system.virt_disk_driver)
        g.launch()

        roots = g.inspect_os()
        mps = g.inspect_get_mountpoints(roots[0])
        for m in mps:
            g.mount(m[1], m[0])

        self.logger.debug("Removing the MAC address from system interfaces")
        newfile="DEVICE=eth0\nONBOOT=yes\nTYPE=Ethernet\nBOOTPROTO=dhcp\n"
        g.write('/etc/sysconfig/network-scripts/ifcfg-eth0',newfile)
        g.rm('/etc/udev/rules.d/70-persistent-net.rules')

        self.logger.debug("Unmounting the guest disk")
        g.umount_all()

        self.logger.debug("Extracting the first partition from the disk for euca2ool use")
        d = parted.disk.Disk(parted.device.Device(path="%s/%s.img" % (directory,system.name)))
        # TODO: raise an error if there's more than one partition
        p = d.partitions[0]
        bsize = 512
        offset = (p.geometry.start * d.device.sectorSize) / bsize
        length = p.getSize(unit="B") / bsize

        dd_cmd = shlex.split("dd if=%s/%s.img of=%s/%s-rootfs.img bs=%d skip=%d count=%d" % (directory,system.name,directory,system.name,bsize,offset,length))
        self.logger.debug("Running '%s'" % dd_cmd)
        po = subprocess.Popen(dd_cmd)
        while po.poll() == None:
            time.sleep(1)

        #if po.returncode != 0:
        #    self.logger.error("The dd command failed, bailing out")
        #    return False

        self.logger.info("Deployment complete")
        return True

