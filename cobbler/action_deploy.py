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
import re
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

    #--------------------------------------------------------------------------------
    # Helper functions
    #--------------------------------------------------------------------------------
 
    def source_envfile(self,envfile):
        """
        Sources a bash resource file, based on the example here:
        http://stackoverflow.com/questions/3503719/emulating-bash-source-in-python
        """
        if not os.path.exists(envfile):
            self.logger.info("The specified envfile ('%s') does not exist" % envfile)
            return False

        lines = utils.subprocess_get(self.logger,shlex.split("bash -c 'source %s && env'" % envfile),shell=False)
        for line in lines.split("\n"):
            (key, _, value) = line.partition("=")
            os.putenv(key,value)
        self.logger.info("Successfully sourced %s" % envfile)
        return True

    #--------------------------------------------------------------------------------
    # Functions for Eucalyptus/Amazon EC2
    #--------------------------------------------------------------------------------
 
    def build_machine_image(self,system,profile,distro,platform,directory):
        """
        Builds an emi/ami compliant image for use with euca2ools
        """
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
            # TODO: this should be a call to utils.subprocess_call
            po = subprocess.Popen(cmd[1])
            while po.poll() == None:
                time.sleep(1)

            if po.returncode != 0:
                self.logger.error("The %s command failed, bailing out" % cmd[0])
                return False

            self.logger.info("%s ok" % cmd[0])
    
        # TODO:
        # use guest fs to extract the kernel and initrd
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
        # TODO: this should be run by utils.subprocess_get
        po = subprocess.Popen(dd_cmd)
        while po.poll() == None:
            time.sleep(1)

        # TODO: parse the dd output for the number of blocks 
        #       copied in and out to verify it matches the 
        #       length variable above, since dd doesn't seem 
        #       to return a proper return code
        return True

    #--------------------------------------------------------------------------------
    # The main entry point
    #--------------------------------------------------------------------------------
 
    # TODO:
    # add a timeout argument
    def run(self,system=None,profile=None,platform=None,directory=None,skip_build=False):
        if not system and not profile:
            return False

        if not directory:
            directory = "/tmp/cobbler_deploy"
        else:
            if not os.path.exists(directory) and not os.path.isdir(directory):
                self.logger.error("ERROR: the output directory (%s) specified is invalid")
                return False

        if platform:
            platform = self.api.get_item("platform",platform)
            self.source_envfile(platform.envfile)

        system = self.api.get_item("system",system)
        profile = system.get_parent()
        distro = profile.get_parent()
        while distro.TYPE_NAME != "distro":
            distro = distro.get_parent()

        self.logger.debug("deploy system  : %s" % system.name)
        self.logger.debug("deploy profile : %s" % profile.name)
        self.logger.debug("deploy distro  : %s" % distro.name)

        if skip_build:
            self.logger.info("skipping the build step...")
        else :
            if platform.type in ("eucalyptus","ec2"):
                if not self.build_machine_image(system,profile,distro,platform,directory):
                    self.logger.error("failed to build the machine image.")
                    return False
            else:
                self.logger.error("Platform type '%s' is not supported yet" % platform.type)
                return False

        self.logger.info("bundling the image...")
        cmd = "euca-bundle-image -i %s/%s-rootfs.img -r %s" % (directory,system.name,distro.arch)
        (output,res) = utils.subprocess_sp(self.logger,cmd)
        if res:
            self.logger.error("The bundling command failed (rval=%d), bailing out. Result:\n%s" % (res,output))
            return False
        self.logger.info("bundling complete")
        
        self.logger.info("uploading the bundle to %s (%s)..." % (platform.name,platform.type))
        cmd = "euca-upload-bundle -b cobbler-image -m %s/%s-rootfs.img.manifest.xml" % (directory,system.name)
        (output,res) = utils.subprocess_sp(self.logger,cmd)
        # TODO: parse output to validate upload worked
        if res:
            self.logger.error("The bundling command failed (rval=%d), bailing out. Result:\n%s" % (res,output))
            return False
        self.logger.info("upload complete, result: %s" % output.strip())

        self.logger.info("registering the image on %s (%s)..." % (platform.name,platform.type) )
        cmd = "euca-register -a %s cobbler-image/%s-rootfs.img.manifest.xml" % (distro.arch,system.name)
        (output,res) = utils.subprocess_sp(self.logger,cmd)
        if res:
            self.logger.error("The bundling command failed (rval=%d), bailing out. Result:\n%s" % (res,output))
            return False
        self.logger.info("registration complete, result: %s" % output.strip())

        self.logger.info("Deployment complete")
        return True

