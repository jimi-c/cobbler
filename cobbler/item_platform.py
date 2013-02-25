"""
Copyright 2010, Kelsey Hightower
Kelsey Hightower <kelsey.hightower@gmail.com>

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

import utils
import item
import os
from cexceptions import CX
from utils import _

# this datastructure is described in great detail in item_distro.py -- read the comments there.

FIELDS = [
    ["uid","",0,"",False,"",0,"str"],
    ["name","",0,"Name",True,"Ex: Amazon EC2",0,"str"],
    ["type","libvirt",0,"Platform Type",True,"What is the platform type?",utils.get_valid_platforms(),"str"],
    ["envfile","",0,"Environment File",True,"Ex: /var/lib/cobbler/platforms/ec2/eucarc",0,"str"],
    ["owners","SETTINGS:default_ownership","SETTINGS:default_ownership","Owners",True,"Owners list for authz_ownership (space delimited)",0,"list"],
    ["comment","",0,"Comment",True,"Free form text description",0,"str"],
    ["ctime",0,0,"",False,"",0,"int"],
    ["mtime",0,0,"",False,"",0,"int"],
]

class Platform(item.Item):

    TYPE_NAME = _("platform")
    COLLECTION_TYPE = "platform"

    def make_clone(self):
        ds = self.to_datastruct()
        cloned = Platform(self.config)
        cloned.from_datastruct(ds)
        return cloned

    def set_type(self,type):
        if type not in utils.get_valid_platforms():
            raise CX(_("%s is not a valid platform, must be one of %s") % (type,str(utils.get_valid_platforms())))
        self.type = type

    def set_envfile(self,envfile):
        if not os.path.exists(envfile):
            raise CX(_("the file '%s' does not appear to exist") % (envfile))
        self.envfile = envfile

    def get_fields(self):
        return FIELDS

    def check_if_valid(self):
        if self.name is None or self.name == "":
            raise CX("name is required")
