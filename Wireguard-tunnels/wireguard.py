#!/usr/bin/python3
#
# Maximilian Wilhelm <max@sdn.clinic>
# -- Sat, 14 May 2022 22:14:47 +0200

from django.utils.text import slugify


from dcim.choices import *
from dcim.models import Device
from dcim.models.device_components import Interface

from extras.models import Tag
from extras.scripts import *

from ipam.choices import *
from ipam.models import IPAddress, Prefix, Role

from virtualization.models import VirtualMachine, VMInterface

import netaddr

prefix_length_by_af = {
	4: 31,
	6: 64,
}

ip_mask_by_af = {
	4: 31,
	6: 126,
}

infra_suffix = ".in.ffho.net"

################################################################################
#                                 Helpers                                      #
################################################################################

class MyException (Exception): {}


def get_prefix_desc (server, client):
	server = server.replace (infra_suffix, "")
	client = client.replace (infra_suffix, "")

	return "%s <-> %s" % (server, client)


def get_iface_name (node_name, tun):
	prefix = "wg"
	if tun['oobm']:
		prefix = "oob"

	node = node_name.replace (infra_suffix, "")
	if_name = "%s-%s" % (prefix, node.replace ('.', '-'))
	if len (if_name) > 15:
		if_name = if_name[0:15]

	return if_name


def node_has_wg_keys_set (node):
	try:
		wg = node.local_context_data['wireguard']
		return wg['privkey'] and wg['pubkey']
	except KeyError:
		return False


################################################################################
#                              Script class                                    #
################################################################################

class AddWireguardTunnel (Script):
	class Meta:
		server_device = "Server (device)"
		server_vm = "Server (VM)"
		client_device = "Client (device)"
		client_vm = "Client (VM)"
		oobm = "Out of Band Mgmt tunnel"
		field_order = ['server_device', 'server_vm', 'client_device', 'client_vm', 'oobm']
		commit_default = False

	# Drop down for server device
	server_device = ObjectVar (
		model = Device,
		required = False,
		query_params = {
			"platform" : 'linux',
		},
		description = "Server end (if device)"
	)

	# Drop down for server VM
	server_vm = ObjectVar (
		model = VirtualMachine,
		required = False,
		query_params = {
			"platform" : 'linux',
		},
		description = "Server end (if VM)"
	)

	# Drop down for client device
	client_device = ObjectVar (
		model = Device,
		required = False,
		query_params = {
			"platform" : 'linux',
		},
		description = "Client end (if device)"
	)

	# Drop down for client VM
	client_vm = ObjectVar (
		model = VirtualMachine,
		required = False,
		query_params = {
			"platform" : 'linux',
		},
		description = "Client end (if VM)"
	)

	oobm = BooleanVar (
		description = "Tunnel should be used for OOBM access to client device"
	)


################################################################################
#                                 Methods                                      #
################################################################################

	def verify_wg_keys_present (self, server, client):
		err = False
		if not node_has_wg_keys_set (server):
			self.log_failure ("Server peer %s does not have Wireguard keys configured in config context!" % server.name)
			err = True
		else:
			self.log_info ("Found Wireguard keys for server peer %s." % server.name)

		if not node_has_wg_keys_set (client):
			self.log_failure ("Client peer [%s](%s) does not have Wireguard keys configured in config context!" % (client.name, client.get_absolute_url ()))
			err = True
		else:
			self.log_info ("Found Wireguard keys for client peer %s." % client.name)

		if err:
			raise MyException ("Pleae configure Wiregurad public and private key in nodes config context.")


	def get_tunnel_prefix (self, server, client, af, oobm):
		pfx_role_slug = "vpn-oobm" if oobm else "vpn-x-connect"
		pfx_role = Role.objects.get (slug = pfx_role_slug)
		desired_plen = prefix_length_by_af[af]
		pfx_desc = get_prefix_desc (server.name, client.name)

		try:
			prefixes = Prefix.objects.filter (
				role = pfx_role,
				is_pool = False,
				status = PrefixStatusChoices.STATUS_ACTIVE,
				description = pfx_desc
			)

			for pfx in prefixes:
				if pfx.family == af:
					self.log_info ("Found existing IPv%s prefix %s." % (af, pfx))
					return pfx
		except Prefix.DoesNotExist:
			pass

		prefixes = Prefix.objects.filter (
			role = pfx_role,
			status = PrefixStatusChoices.STATUS_CONTAINER,
			is_pool = False
		)

		for pfx in prefixes:
			if pfx.family != af:
				continue

			msg = "Found IPv%s container %s, " % (af, pfx.prefix)

			# Get a list of all availble sub prefixes (type IPNetwork)
			avail_pfxs = pfx.get_available_prefixes ().iter_cidrs ()
			for apfx in avail_pfxs:
				if apfx.prefixlen <= desired_plen:
					new_prefix = Prefix (
						prefix = "%s/%s" % (apfx.network, desired_plen),
						role = pfx_role,
						description = pfx_desc
					)

					new_prefix.save ()
					msg += "picking %s for new tunnel." % new_prefix
					self.log_success (msg)

					return new_prefix

			msg += "but no free prefixes available *sniff*"
			self.log_info (msg)

		raise MyException ("Can't find IPv%s prefix to carve transfer network from, dying of shame." % af)


	# TODO: Query interfaces only by object + if_name and validate Wireguard Tag + type:Virtual (for devices) here
	def validate_interface (self, iface, node, peer):
		# Custom field name relevant for peer
		peer_type = 'device' if type (peer) == Device else 'vm'
		cf_name = 'wg_peer_%s' % peer_type

		# Custom field name which should be empty
		unused_peer_type = 'vm' if type (peer) == Device else 'device'
		unused_cf_name = 'wg_peer_%s' % unused_peer_type

		# Got interface, check if unused CF is empty
		if iface.custom_field_data.get ('unused_cf_name'):
			raise MyException ("Found interface '%s' on node '%s', but it's linked to somewhere else, check %s custom field!" % (iface, node.name, unused_cf_name))

		# Cool, is the correct CF filled?
		if_peer = iface.custom_field_data[cf_name]
		if not if_peer:
			iface.custom_field_data[cf_name] = peer.id
			iface.save ()
			self.log_success ("Found interface '%s' on node '%s' and linked it to peer '%s'" % (iface, node.name, peer.name))
			return

		if if_peer != peer.id:
			raise MyException ("Found interface '%s' on node '%s', but it's linked to somewhere else, check %s custom field!" % (iface, node.name, cf_name))

		self.log_info ("Found interface '%s' on node '%s' linked to peer '%s', carrying on." % (iface, node.name, peer.name))


	def create_interface (self, tun, node, peer):
		peer_type = 'device' if type (peer) == Device else 'vm'
		cf_name = 'wg_peer_%s' % peer_type
		if_name = get_iface_name (peer.name, tun)

		try:
			wg_tag = Tag.objects.get (
				name = "Wireguard"
			)
		except Tag.DoesNotExist:
			raise MyException ("Wiregurad tag doesn't exist, dying of shame.")

		# Physical device
		if type (node) == Device:
			try:
				iface= Interface.objects.get (
					device = node,
					name = if_name,
					type = InterfaceTypeChoices.TYPE_VIRTUAL,
					tags = wg_tag,
				)

				self.validate_interface (iface, node, peer)
			except Interface.DoesNotExist:
				iface = Interface (
					device = node,
                                        name = if_name,
                                        type = InterfaceTypeChoices.TYPE_VIRTUAL,
					custom_field_data = { cf_name : peer.id }
				)
				iface.save ()
				iface.tags.add (wg_tag)

				self.log_success ("Created interface '%s' on peer '%s'." % (iface, node.name))

			return iface

		# Virtual Machine
		elif type (node) == VirtualMachine:
			try:
				iface = VMInterface.objects.get (
					virtual_machine = node,
					name = if_name,
					tags = wg_tag
				)

				self.validate_interface (iface, node, peer)
			except VMInterface.DoesNotExist:
				iface = VMInterface (
					virtual_machine = node,
					name = if_name,
					custom_field_data = { cf_name : peer.id },
				)
				iface.save ()
				iface.tags.add (wg_tag)

				self.log_success ("Created interface '%s' on peer '%s'." % (iface, node.name))
			return iface

		raise MyException ("What device type is %s? Don't know what to do with it, sorry." % node.name)


	def configure_ip (self, node, iface, ip_str, plen):
		ip, created = IPAddress.objects.get_or_create (address = "%s/%s" % (ip_str, plen))
		if created:
			ip.save ()
			iface.ip_addresses.add (ip)
			iface.save ()

			self.log_success ("Configured IP %s on interface %s on %s " % (ip, iface, node))
			return

		# IP existed
		msg = "IP address %s for interface %s on %s already existed" % (ip, iface, node)
		if ip.assigned_object:
			self.log_info ("IP %s already exists and assigned to interface %s on %s" % (ip, ip.assigned_object, node))


	def configure_ips (self, tunnel):
		pfxs = tunnel['prefix']
		ips = tunnel['ips']

		for af in [ 4, 6 ]:
			pfx = netaddr.IPNetwork (pfxs[af].prefix)

			if af == 4:
				ips = [
					netaddr.IPAddress (pfx.first),
					netaddr.IPAddress (pfx.first + 1)
				]
			else:
				ips = [
					netaddr.IPAddress (pfx.first + 1),
					netaddr.IPAddress (pfx.first + 2)
				]

			# Server
			self.configure_ip (tunnel['server'], tunnel['iface']['server'], ips[0], ip_mask_by_af[af])

			# Client
			self.configure_ip (tunnel['client'], tunnel['iface']['client'], ips[1], ip_mask_by_af[af])


	def configure_tunnel (self, server, client, oobm):
		# Do the peers have Wireguard keys set in config context?
		self.verify_wg_keys_present (server, client)

		tun = {
			"server" : server,
			"client" : client,
			"oobm" : oobm,
			"iface" : {
				"server" : None,
				"client" : None
			},
			"prefix" : {
				4 : None,
				6 : None
			},
			"ips" : {
				"server" : {
					4 : None,
					6 : None
				},
				"client" : {
					4 : None,
					6 : None
				}
			}
		}

		# Create the IPv4 + IPv6 transfer network, if not already present
		for af in [ 4, 6 ]:
			tun['prefix'][af] = self.get_tunnel_prefix (server, client, af, oobm)

		tun['iface']['server'] = self.create_interface (tun, server, client)
		tun['iface']['client'] = self.create_interface (tun, client, server)

		self.configure_ips (tun)

		return tun


	def run (self, data, commit):
		server_device = data['server_device']
		server_vm = data['server_vm']
		client_device = data['client_device']
		client_vm = data['client_vm']
		oobm = data['oobm']

		# Validate if input makes sense, we need one server and one client peer
		if (server_device and server_vm):
			self.log_failure ("Server device and VM selected, choose only one!")
			return "D'oh!"

		if (client_device and client_vm):
			self.log_failure ("Client device and VM selected, choose only one!")
			return "D'oh!"

		server = server_device if server_device else server_vm
		client = client_device if client_device else client_vm

		if not (server and client):
			self.log_failure ("At least one server and one client peer must be given!")
			return "D'oh!"


		try:
			self.configure_tunnel (server, client, oobm)
		except MyException as m:
			return m

