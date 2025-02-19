# Copyright 2014 Juniper Networks.  All rights reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# @author: Hampapur Ajay, Praneet Bachheti, Rudra Rugge, Atul Moghe

try:
    from neutron.api.v2.attributes import ATTR_NOT_SPECIFIED
except Exception:
    from neutron_lib.constants import ATTR_NOT_SPECIFIED
try:
    from neutron.common.exceptions import ServiceUnavailable
except ImportError:
    from neutron_lib.exceptions import ServiceUnavailable
try:
    from neutron.common.exceptions import NeutronException
except ImportError:
    from neutron_lib.exceptions import NeutronException
try:
    from neutron.common.exceptions import NotAuthorized
except ImportError:
    from neutron_lib.exceptions import NotAuthorized
try:
    from neutron.common import exceptions as neutron_exc
except ImportError:
    from neutron_lib import exceptions as neutron_exc
try:
    from neutron_lib import exceptions as neutron_lib_exc
except ImportError:
    neutron_lib_exc = None
try:
    from neutron_lib.exceptions import l3 as neutron_lib_l3_exc
except ImportError:
    neutron_lib_l3_exc = None
try:
    from neutron_lib.exceptions import firewall_v2 as firewall_v2_exc
except ImportError:
    firewall_v2_exc = None
try:
    from neutron.services.trunk import exceptions as neutron_trunk_exc
except ImportError:
    neutron_trunk_exc = None
try:
    from oslo.config import cfg
except ImportError:
    from oslo_config import cfg
try:
    from neutron.extensions import portsecurity as port_security_extn
except ImportError:
    from neutron_lib.exceptions import portsecurity as port_security_extn

from neutron import version
from neutron.db import portbindings_base
from neutron.extensions import allowedaddresspairs
from neutron.extensions import external_net
from neutron.extensions import l3
from neutron.extensions import netmtu
from neutron.extensions import netmtu_writable
from neutron.extensions import portbindings
from neutron.extensions import securitygroup
from neutron_plugin_contrail.extensions import serviceinterface
from neutron_plugin_contrail.extensions import vfbinding
from neutron import neutron_plugin_base_v2
try:
    from neutron.openstack.common import importutils
except ImportError:
    from oslo_utils import importutils

try:
    from neutron.openstack.common import log as logging
except ImportError:
    from oslo_log import log as logging
try:
    from neutron.callbacks import events
except ImportError:
    from neutron_lib.callbacks import events
try:
    from neutron.callbacks import registry
except ImportError:
    from neutron_lib.callbacks import registry
try:
    from neutron.callbacks import resources
except ImportError:
    from neutron_lib.callbacks import resources

from neutron_plugin_contrail.common import utils

# Constant for max length of network interface names
# eg 'bridge' in the Network class or 'devname' in
# the VIF class
NIC_NAME_LEN = 14

VIF_TYPE_VROUTER = 'vrouter'

LOG = logging.getLogger(__name__)

NEUTRON_CONTRAIL_PREFIX = 'NEUTRON'


def _raise_contrail_error(info, obj_name):
    exc_name = info.get('exception', 'No exception name provided')

    if exc_name == 'VirtualRouterNotFound':
        raise HttpResponseError(info)
    elif exc_name == 'NotAuthorized':
        raise NotAuthorized(**info)
    elif str(exc_name) == 'OverQuota':
        info['exception'] = str(info['exception'])
        if 'msg' in info:
            info['msg'] = str(info['msg'])
        if 'overs' in info:
            info['overs'] = [str(info['overs'][0])]
    elif exc_name == 'BadRequest' and 'resource' not in info:
        info['resource'] = obj_name

    if hasattr(neutron_exc, exc_name):
        raise getattr(neutron_exc, exc_name)(**info)
    elif hasattr(l3, exc_name):
        raise getattr(l3, exc_name)(**info)
    elif hasattr(securitygroup, exc_name):
        raise getattr(securitygroup, exc_name)(**info)
    elif hasattr(allowedaddresspairs, exc_name):
        raise getattr(allowedaddresspairs, exc_name)(**info)
    elif hasattr(port_security_extn, exc_name):
        raise getattr(port_security_extn, exc_name)(**info)
    elif neutron_lib_exc and hasattr(neutron_lib_exc, exc_name):
        raise getattr(neutron_lib_exc, exc_name)(**info)
    elif neutron_lib_l3_exc and hasattr(neutron_lib_l3_exc, exc_name):
        raise getattr(neutron_lib_l3_exc, exc_name)(**info)
    elif firewall_v2_exc and hasattr(firewall_v2_exc, exc_name):
        raise getattr(firewall_v2_exc, exc_name)(**info)
    elif neutron_trunk_exc and hasattr(neutron_trunk_exc, exc_name):
        raise getattr(neutron_trunk_exc, exc_name)(**info)
    else:
        try:
            raise NeutronException(**info)
        except Exception:
            LOG.exception("Contrail raised unknown exception '%s' with args: "
                          "%s", exc_name, info)


class InvalidContrailExtensionError(ServiceUnavailable):
    message = "Invalid Contrail Extension: %(ext_name) %(ext_class)"


class HttpResponseError(Exception):
    def __init__(self, resp_info):
        self.response_info = resp_info


class NeutronPluginContrailCoreBase(neutron_plugin_base_v2.NeutronPluginBaseV2,
                                    securitygroup.SecurityGroupPluginBase,
                                    portbindings_base.PortBindingBaseMixin,
                                    external_net.External_net,
                                    serviceinterface.Serviceinterface,
                                    vfbinding.Vfbinding,
                                    netmtu.Netmtu,
                                    netmtu_writable.Netmtu_writable):

    supported_extension_aliases = [
        "standard-attr-description",
        "security-group",
        "router",
        "port-security",
        "binding",
        "quotas",
        "external-net",
        "allowed-address-pairs",
        "extra_dhcp_opt",
        "provider",
        "net-mtu-writable",
        "net-mtu"
    ]

    __native_bulk_support = False

    # TODO(md): This should be added in upstream (neutron portbindings
    # extension) instead of patching it here. This constants are in newer
    # versions of neutron, but not in the Kilo verion.
    portbindings.__dict__['VIF_TYPE_VHOST_USER'] = 'vhostuser'

    def _parse_class_args(self):
        """Parse the contrailplugin.ini file.

        Opencontrail supports extension such as ipam, policy, these extensions
        can be configured in the plugin configuration file as shown below.
        Plugin then loads the specified extensions.
        contrail_extensions=ipam:<classpath>,policy:<classpath>
        """

        contrail_extensions = cfg.CONF.APISERVER.contrail_extensions
        # If multiple class specified for same extension, last one will win
        # according to DictOpt behavior
        for ext_name, ext_class in contrail_extensions.items():
            try:
                if not ext_class or ext_class == 'None':
                    self.supported_extension_aliases.append(ext_name)
                    continue
                ext_class = importutils.import_class(ext_class)
                ext_instance = ext_class()
                ext_instance.set_core(self)
                for method in dir(ext_instance):
                    for prefix in ['get', 'update', 'delete', 'create']:
                        if method.startswith('%s_' % prefix):
                            setattr(self, method,
                                    ext_instance.__getattribute__(method))
                self.supported_extension_aliases.append(ext_name)
            except Exception:
                LOG.exception("Contrail Backend Error")
                # Converting contrail backend error to Neutron Exception
                raise InvalidContrailExtensionError(
                    ext_name=ext_name, ext_class=ext_class)
        self._build_auth_details()

    def _build_auth_details(self):
        pass

    def __init__(self):
        # some extensions should be added only for supported versions
        if (int(version.version_info.version_string().split('.')[0]) >= 13 and
                "port-mac-address-regenerate" not in self.supported_extension_aliases):
            self.supported_extension_aliases.append("port-mac-address-regenerate")
        super(NeutronPluginContrailCoreBase, self).__init__()
        if hasattr(portbindings_base, 'register_port_dict_function'):
            portbindings_base.register_port_dict_function()
        utils.register_vnc_api_options()
        self._parse_class_args()
        self.api_servers = utils.RoundRobinApiServers()

    def _create_resource(self, res_type, context, res_data):
        pass

    def _get_resource(self, res_type, context, id, fields):
        pass

    def _update_resource(self, res_type, context, id, res_data):
        pass

    def _delete_resource(self, res_type, context, id):
        pass

    def _list_resource(self, res_type, context, filters, fields):
        pass

    def _count_resource(self, res_type, context, filters):
        pass

    def _get_network(self, context, id, fields=None):
        return self._get_resource('network', context, id, fields)

    def create_network(self, context, network):
        """Creates a new Virtual Network."""
        return self._create_resource('network', context, network)

    def get_network(self, context, network_id, fields=None):
        """Get the attributes of a particular Virtual Network."""

        return self._get_network(context, network_id, fields)

    def update_network(self, context, network_id, network):
        """Updates the attributes of a particular Virtual Network."""

        return self._update_resource('network', context, network_id,
                                     network)

    def delete_network(self, context, network_id):
        """Creates a new Virtual Network.

        Deletes the network with the specified network identifier
        belonging to the specified tenant.
        """

        self._delete_resource('network', context, network_id)

    def get_networks(self, context, filters=None, fields=None):
        """Get the list of Virtual Networks."""

        return self._list_resource('network', context, filters,
                                   fields)

    def get_networks_count(self, context, filters=None):
        """Get the count of Virtual Network."""

        networks_count = self._count_resource('network', context, filters)
        return networks_count['count']

    def create_subnet(self, context, subnet):
        """Creates a new subnet, and assigns it a symbolic name."""

        if subnet['subnet']['gateway_ip'] is None:
            gateway = '0.0.0.0'
            if subnet['subnet']['ip_version'] == 6:
                gateway = '::'
            subnet['subnet']['gateway_ip'] = gateway

        if subnet['subnet']['host_routes'] != ATTR_NOT_SPECIFIED:
            if (len(subnet['subnet']['host_routes']) >
                    cfg.CONF.max_subnet_host_routes):
                raise neutron_exc.HostRoutesExhausted(subnet_id=subnet[
                    'subnet'].get('id', 'new subnet'),
                    quota=cfg.CONF.max_subnet_host_routes)

        subnet_created = self._create_resource('subnet', context, subnet)
        return self._make_subnet_dict(subnet_created)

    def _make_subnet_dict(self, subnet):
        return subnet

    def _get_subnet(self, context, subnet_id, fields=None):
        subnet = self._get_resource('subnet', context, subnet_id, fields)
        return self._make_subnet_dict(subnet)

    def get_subnet(self, context, subnet_id, fields=None):
        """Get the attributes of a particular subnet."""

        return self._get_subnet(context, subnet_id, fields)

    def update_subnet(self, context, subnet_id, subnet):
        """Updates the attributes of a particular subnet."""

        subnet = self._update_resource('subnet', context, subnet_id, subnet)
        return self._make_subnet_dict(subnet)

    def delete_subnet(self, context, subnet_id):
        """
        Deletes the subnet with the specified subnet identifier
        belonging to the specified tenant.
        """

        self._delete_resource('subnet', context, subnet_id)

    def get_subnets(self, context, filters=None, fields=None):
        """Get the list of subnets."""

        return [self._make_subnet_dict(s)
                for s in self._list_resource(
                    'subnet', context, filters, fields)]

    def get_subnets_count(self, context, filters=None):
        """Get the count of subnets."""

        subnets_count = self._count_resource('subnet', context, filters)
        return subnets_count['count']

    def _extend_port_dict_security_group(self, port_res, port_db):
        # Security group bindings will be retrieved from the sqlalchemy
        # model. As they're loaded eagerly with ports because of the
        # joined load they will not cause an extra query.
        port_res[securitygroup.SECURITYGROUPS] = port_db.get(
            'security_groups', []) or []
        return port_res

    def _make_port_dict(self, port, fields=None):
        """filters attributes of a port based on fields."""

        vhostuser = (
            portbindings.VIF_TYPE in port and
            port[portbindings.VIF_TYPE] == portbindings.VIF_TYPE_VHOST_USER)

        if not fields:
            port.update(self.base_binding_dict)
        else:
            for key in self.base_binding_dict:
                if key in fields:
                    port[key] = self.base_binding_dict[key]

        # Update bindings for vhostuser vif support
        if vhostuser:
            self._update_vhostuser_cfg_for_port(port)

        return port

    def _get_port(self, context, id, fields=None):
        return self._get_resource('port', context, id, fields)

    def _update_ips_for_port(self, context, network_id, port_id, original_ips,
                             new_ips):
        """Add or remove IPs from the port."""

        # These ips are still on the port and haven't been removed
        prev_ips = []

        # Remove all of the intersecting elements
        for original_ip in original_ips[:]:
            for new_ip in new_ips[:]:
                if ('ip_address' in new_ip and
                        original_ip['ip_address'] == new_ip['ip_address']):
                    original_ips.remove(original_ip)
                    new_ips.remove(new_ip)
                    prev_ips.append(original_ip)

        return new_ips, prev_ips

    def _get_vrouter_config(self, context, id, fields=None):
        return self._get_resource('virtual_router', context, id, fields)

    def _list_vrouters(self, context, filters=None, fields=None):
        return self._list_resource('virtual_router', context, filters, fields)

    def create_port(self, context, port):
        """Creates a port on the specified Virtual Network."""

        port = self._create_resource('port', context, port)
        return port

    def get_port(self, context, id, fields=None):
        """Get the attributes of a particular port."""

        return self._get_port(context, id, fields)

    def update_port(self, context, port_id, port):
        """Updates a port.

        Updates the attributes of a port on the specified Virtual
        Network.
        """

        original_port = self._get_port(context, port_id)
        if 'fixed_ips' in port['port']:
            added_ips, prev_ips = self._update_ips_for_port(
                context, original_port['network_id'], port_id,
                original_port['fixed_ips'], port['port']['fixed_ips'])
            port['port']['fixed_ips'] = prev_ips + added_ips

        port = self._update_resource('port', context, port_id, port)
        project_id = port.get('tenant_id') or port.get('project_id')
        port['tenant_id'] = port['project_id'] = project_id
        kwargs = {
            'context': context,
            'port': port,
            'original_port': original_port,
        }
        registry.notify(resources.PORT, events.AFTER_UPDATE, self, **kwargs)
        return port

    def delete_port(self, context, port_id):
        """Deletes a port.

        Deletes a port on a specified Virtual Network,
        if the port contains a remote interface attachment,
        the remote interface is first un-plugged and then the port
        is deleted.
        """

        self._delete_resource('port', context, port_id)

    def get_ports(self, context, filters=None, fields=None):
        """Get all ports.

        Retrieves all port identifiers belonging to the
        specified Virtual Network with the specfied filter.
        """

        return self._list_resource('port', context, filters, fields)

    def get_ports_count(self, context, filters=None):
        """Get the count of ports."""

        ports_count = self._count_resource('port', context, filters)
        return ports_count['count']

    # Router API handlers
    def create_router(self, context, router):
        """Creates a router.

        Creates a new Logical Router, and assigns it
        a symbolic name.
        """

        return self._create_resource('router', context, router)

    def get_router(self, context, router_id, fields=None):
        """Get the attributes of a router."""

        return self._get_resource('router', context, router_id, fields)

    def update_router(self, context, router_id, router):
        """Updates the attributes of a router."""

        return self._update_resource('router', context, router_id,
                                     router)

    def delete_router(self, context, router_id):
        """Deletes a router."""

        self._delete_resource('router', context, router_id)

    def get_routers(self, context, filters=None, fields=None):
        """Retrieves all router identifiers."""

        return self._list_resource('router', context, filters, fields)

    def get_routers_count(self, context, filters=None):
        """Get the count of routers."""

        routers_count = self._count_resource('router', context, filters)
        return routers_count['count']

    def add_router_interface(self, context, router_id, interface_info):
        pass

    def remove_router_interface(self, context, router_id, interface_info):
        pass

    # Floating IP API handlers
    def create_floatingip(self, context, floatingip):
        """Creates a floating IP."""

        return self._create_resource('floatingip', context, floatingip)

    def update_floatingip(self, context, fip_id, floatingip):
        """Updates the attributes of a floating IP."""

        return self._update_resource('floatingip', context, fip_id,
                                     floatingip)

    def get_floatingip(self, context, fip_id, fields=None):
        """Get the attributes of a floating ip."""

        return self._get_resource('floatingip', context, fip_id, fields)

    def delete_floatingip(self, context, fip_id):
        """Deletes a floating IP."""

        self._delete_resource('floatingip', context, fip_id)

    def get_floatingips(self, context, filters=None, fields=None):
        """Retrieves all floating ips identifiers."""

        return self._list_resource('floatingip', context, filters, fields)

    def get_floatingips_count(self, context, filters=None):
        """Get the count of floating IPs."""

        fips_count = self._count_resource('floatingip', context, filters)
        return fips_count['count']

    # Security Group handlers
    def create_security_group(self, context, security_group):
        """Creates a Security Group."""

        return self._create_resource('security_group', context,
                                     security_group)

    def get_security_group(self, context, sg_id, fields=None, tenant_id=None):
        """Get the attributes of a security group."""

        return self._get_resource('security_group', context, sg_id, fields)

    def update_security_group(self, context, sg_id, security_group):
        """Updates the attributes of a security group."""

        return self._update_resource('security_group', context, sg_id,
                                     security_group)

    def delete_security_group(self, context, sg_id):
        """Deletes a security group."""

        self._delete_resource('security_group', context, sg_id)

    def get_security_groups(self, context, filters=None, fields=None,
                            sorts=None, limit=None, marker=None,
                            page_reverse=False):
        """Retrieves all security group identifiers."""

        return self._list_resource('security_group', context,
                                   filters, fields)

    def get_security_groups_count(self, context, filters=None):
        return 0

    def get_security_group_rules_count(self, context, filters=None):
        return 0

    def create_security_group_rule(self, context, security_group_rule):
        """Creates a security group rule."""

        return self._create_resource('security_group_rule', context,
                                     security_group_rule)

    def delete_security_group_rule(self, context, sg_rule_id):
        """Deletes a security group rule."""

        self._delete_resource('security_group_rule', context, sg_rule_id)

    def get_security_group_rule(self, context, sg_rule_id, fields=None):
        """Get the attributes of a security group rule."""

        return self._get_resource('security_group_rule', context,
                                  sg_rule_id, fields)

    def get_security_group_rules(self, context, filters=None, fields=None,
                                 sorts=None, limit=None, marker=None,
                                 page_reverse=False):
        """Retrieves all security group rules."""

        return self._list_resource('security_group_rule', context,
                                   filters, fields)
