#!/usr/bin/python3
#
# Maximilian Wilhelm <max@sdn.clinic>
#  --  Tue 19 May 2020 09:29:42 PM CEST
#

from django.utils.text import slugify

from dcim.choices import *
from dcim.models import Cable, Device, DeviceRole, DeviceType, Platform, Rack, RackRole, Site
from dcim.models.device_components import FrontPort, Interface, RearPort

from ipam.choices import *
from ipam.models import IPAddress, Prefix, Role, VLAN

from extras.scripts import *


class ProvisionBackbonePOP (Script):
	class Meta:
		name = "Provision Backbone POP"
		description = "Provision a new backbone POP"
		field_order = ['site', 'site_no', 'rack_name', 'rack_units', 'panel_ports', 'pole_setup']
		commit_default = False

	# Drop down for sites
	site = ObjectVar (
		description = "Site to be deployed",
		queryset = Site.objects.all ()
	)

	# Site No.
	site_no = IntegerVar (description = "Site number (for Mgmt VLAN + prefix)")

	# Rack name
	rack_name = StringVar (description = "Name of the rack")

	# Rack units
	rack_units = IntegerVar (description = "Number of units of this rack")

	# BBR Asset Tag
	bbr_asset_tag = StringVar (description = "Asset tag of backbone router")

	# Switch asset tag
	sw_asset_tag = StringVar (description = "Asset tag of switch")

	# Panel ports
	panel_ports = IntegerVar (description = "Number of port on the patch panel (if 19\")")

	# Pole setup
	pole_setup = StringVar (description = "Space separated list of &lt;pole no&gt;:&lt;num_cables&gt;")

	# BBR ID
	node_id = IntegerVar (description = "Node ID of BBR")


################################################################################
#                                 Methods                                      #
################################################################################

	def create_mgmt_vlan (self, site, site_no):
		vlan_id = 3000 + int (site_no)
		try:
			vlan = VLAN.objects.get (site = site, vid = vlan_id)
			self.log_info ("Mgmt vlan %s already present, carrying on." % vlan)

			return vlan
		except VLAN.DoesNotExist:
			pass

		vlan = VLAN (
			site = site,
			name = "Mgmt %s" % site.name,
			vid = vlan_id,
			role = Role.objects.get (name = 'Mgmt')
		)

		vlan.save ()
		self.log_success ("Created mgmt VLAN %s" % vlan)

		return vlan


	def create_mgmt_prefix (self, site, site_no, vlan):
		prefix_cidr = "172.30.%d.0/24" % site_no
		try:
			prefix = Prefix.objects.get (prefix = prefix_cidr)
			self.log_info ("Mgmt prefix %s already present, carrying on." % prefix)

			return prefix
		except Prefix.DoesNotExist:
			pass

		prefix = Prefix (
			site = site,
			prefix = prefix_cidr,
			vlan = vlan,
			role = Role.objects.get (name = 'Mgmt')
		)

		prefix.save ()
		self.log_success ("Created mgmt prefix %s" % prefix)

		return prefix


	def create_rack (self, site, name, units):
		try:
			rack = Rack.objects.get (name = name)
			self.log_info ("Rack %s already present, carrying on." % rack)
			return rack
		except Rack.DoesNotExist:
			pass

		rack = Rack (
			role = RackRole.objects.get (name = 'Backbone'),
			type = RackTypeChoices.TYPE_WALLCABINET,
			width = RackWidthChoices.WIDTH_19IN,
			u_height = units,
			status = RackStatusChoices.STATUS_PLANNED,
			name = name,
			site = site
		)

		rack.save ()
		self.log_success ("Created rack {}".format (rack))
		return rack


	def create_patch_panel (self, site, rack, name, ports):
		pp_name = "pp-%s-%s.1" % (site.slug, name)

		try:
			pp = Device.objects.get (name = pp_name)
			self.log_info ("Patch panel %s already present, carrying on." % pp)
			return pp
		except Device.DoesNotExist:
			pass

		pp_type = DeviceType.objects.get (
			manufacturer__name = 'Telegärtner',
			model = 'Patchpanel'
		)

		pp = Device (
			device_type = pp_type,
			device_role = DeviceRole.objects.get (name = 'Patchpanel'),
			site = site,
			status = DeviceStatusChoices.STATUS_PLANNED,
			name = pp_name,
			rack = rack,
			position = rack.u_height,
			face = DeviceFaceChoices.FACE_FRONT
		)

		pp.save ()
		self.log_success ("Created patch panel {}".format (pp))

		# Create front and rear ports
		for n in range (1, int (ports) + 1):
			rear_port = RearPort (
				device = pp,
				name = str (n),
				type = PortTypeChoices.TYPE_8P8C,
				positions = 1
			)
			rear_port.save ()

			front_port = FrontPort (
				device = pp,
				name = str (n),
				type = PortTypeChoices.TYPE_8P8C,
				rear_port = rear_port,
				rear_port_position = 1,
			)
			front_port.save ()

		return pp


	def create_and_connect_surges (self, site, rack, pp, pole_setup):
		# surge_config will be of format <pole no>:<num surges>[ <pole no>:<num surges> [...]]
		# So first split by spaces to get a single pole config and then iterate of surge at this pole.
		# The RearPort of the 1st surge protector of the 1st pole will be connected to PP port 1 then
		# continuing upwards.
		surge_type = DeviceType.objects.get (
			manufacturer__name = 'Ubnt',
			model = 'Surge Protector'
		)

		pp_port = 1
		for pole_config in pole_setup.split ():
			pole_no, num_surges = pole_config.split (':')

			for n in range (1, int (num_surges) + 1):
				# Create surge
				surge_name = "sp-%s-mast%s-%s" % (site.slug.lower (), pole_no, n)
				surge = Device (
					device_type = surge_type,
					device_role = DeviceRole.objects.get (name = 'Surge Protector'),
					name = surge_name,
					status = DeviceStatusChoices.STATUS_PLANNED,
					site = site
				)

				surge.save ()

				# Link RearPort of SP to next free panel port
				cable = Cable (
					termination_a = RearPort.objects.get (device = pp, name = str (pp_port)),
					termination_b = RearPort.objects.get (device = surge, name = str (1)),
					status = CableStatusChoices.STATUS_PLANNED
				)

				cable.save ()
				self.log_success ("Created surge protector %s and linked it to patch panel port %s." % (surge, pp_port))

				pp_port += 1


	def setup_swtich (self, site, rack, pp, panel_ports, vlan, site_no, asset_tag):
		sw_name = "sw-%s-01.in.ffho.net" % site.slug

		try:
			sw = Device.objects.get (name = sw_name)
			self.log_info ("Switch %s already present, carrying on." % sw_name)

			return sw
		except Device.DoesNotExist:
			pass

		sw_type = DeviceType.objects.get (
			manufacturer__name = 'Netonix',
			model = 'WS-12-250-AC'
		)

		sw = Device (
			device_type = sw_type,
			device_role = DeviceRole.objects.get (name = 'Switch'),
			platform = Platform.objects.get (name = 'Netonix'),
			name = sw_name,
			asset_tag = asset_tag,
			status = DeviceStatusChoices.STATUS_PLANNED,
			site = site,
			rack = rack,
			position = rack.u_height - 2,
			face = DeviceFaceChoices.FACE_FRONT
		)

		sw.save ()
		self.log_success ("Created switch %s" % sw)

		# Link switch ports for panel ports
		for n in range (1, int (panel_ports) + 1):
			cable = Cable (
				termination_a = Interface.objects.get (device = sw, name = str (n)),
				termination_b = FrontPort.objects.get (device = pp, name = str (n)),
				status = CableStatusChoices.STATUS_PLANNED
			)
			cable.save ()

		# Disable interfaces which aren't connected
		unused_ifaces = [13, 14]
		if panel_ports < 10:
			unused_ifaces.extend (list (range (int (panel_ports + 1), 10)))
		unused_ifaces = [str (x) for x in sorted (unused_ifaces)]
		for n in unused_ifaces:
			iface = Interface.objects.get (device = sw, name = n)
			iface.enabled = False
			iface.save ()

		self.log_success ("Disabled switch unsued ports %s" % ",".join (unused_ifaces))

		# Set up Mgmt port
		sw_mgmt_port = Interface.objects.get (device = sw, name = "10")
		sw_mgmt_port.mode = InterfaceModeChoices.MODE_ACCESS
		sw_mgmt_port.untagged_vlan = vlan
		sw_mgmt_port.description = "Mgmt"
		sw_mgmt_port.save ()

		self.log_success ("Set mgmt interface 10 to untagged VLAN %s" % vlan)

		# Set po1 tagged-all and bundle ports 11 + 12 into it
		sw_po1 = Interface.objects.get (device = sw, name = 'po1')
		sw_po1.mode = InterfaceModeChoices.MODE_TAGGED_ALL
		sw_po1.save ()

		for n in [ 11, 12 ]:
			sw_port = Interface.objects.get (device = sw, name = str (n))
			sw_port.lag = sw_po1
			sw_port.save ()

		self.log_success ("Linked first %s ports of %s to %s" % (panel_ports, sw, pp))

		# Set up Mgmt vlan interface + IP
		sw_mgmt_iface = Interface (
			device = sw,
			name = "vlan%d" % vlan.vid,
			type = InterfaceTypeChoices.TYPE_VIRTUAL,
		)
		sw_mgmt_iface.save ()

		sw_mgmt_ip = IPAddress (
			address = "172.30.%d.10/24" % site_no,
			interface = sw_mgmt_iface
		)
		sw_mgmt_ip.save ()

		sw.primary_ip4 = sw_mgmt_ip
		sw.save ()

		self.log_success ("Configured %s on interface %s of %s" % (sw_mgmt_ip, sw_mgmt_iface, sw))

		return sw


	def setup_bbr (self, site, rack, vlan, site_no, node_id, asset_tag, sw):
		bbr_name = "bbr-%s.in.ffho.net" % site.slug

		try:
			bbr = Device.objects.get (name = bbr_name)
			self.log_info ("Backbone router %s already present, carrying on." % bbr_name)

			return bbr
		except Device.DoesNotExist:
			pass

		bbr_type = DeviceType.objects.get (
			manufacturer__name = 'PCEngines',
			model = 'APU2c4-19"'
		)

		bbr = Device (
			device_type = bbr_type,
			device_role = DeviceRole.objects.get (name = 'Backbone router'),
			platform = Platform.objects.get (name = 'Linux'),
			name = bbr_name,
			asset_tag = asset_tag,
			status = DeviceStatusChoices.STATUS_PLANNED,
			site = site,
			rack = rack,
			position = rack.u_height - 4,
			face = DeviceFaceChoices.FACE_FRONT,
		)

		bbr.save ()
		self.log_success ("Created backbone router %s" % bbr)

		# Set bond0 mode to tagged-all, bundle enp<n>s0 into it and connect enp<n>s0  to switchport 10 + n
		bbr_bond0 = Interface.objects.get (device = bbr, name = "bond0")
		bbr_bond0.mode = InterfaceModeChoices.MODE_TAGGED_ALL
		bbr_bond0.save ()

		# Link enp1s0 and enp2s0 to switch port 10 and 11 respectivly
		for n in [1, 2]:
			bbr_port = Interface.objects.get (device = bbr, name = "enp%ds0" % n)
			sw_port = Interface.objects.get (device = sw, name = str (10 + n))
			cable = Cable (
				termination_a = sw_port,
				termination_b = bbr_port,
				status = CableStatusChoices.STATUS_PLANNED
			)
			cable.save ()

			bbr_port.lag = bbr_bond0
			bbr_port.save ()

		self.log_success ("Linked %s to %s" % (bbr, sw))

		# Disable enp3s0
		enp3s0 = Interface.objects.get (device = bbr, name = "enp3s0")
		enp3s0.enabled = False
		enp3s0.save ()

		# Set up Mgmt vlan interface + IP
		bbr_mgmt_iface = Interface (
			device = bbr,
			name = "vlan%d" % vlan.vid,
			type = InterfaceTypeChoices.TYPE_VIRTUAL,
		)
		bbr_mgmt_iface.save ()

		bbr_mgmt_ip = IPAddress (
			address = "172.30.%d.1/24" % site_no,
			interface = bbr_mgmt_iface
		)
		bbr_mgmt_ip.save ()

		self.log_success ("Configured %s on interface %s of %s" % (bbr_mgmt_ip, bbr_mgmt_iface, bbr))

		# Set up loopback IPs
		bbr_lo_iface = Interface.objects.get (device = bbr, name = "lo")
		ipv4 = IPAddress (
			address = "10.132.255.%s/32" % node_id,
			interface = bbr_lo_iface
		)
		ipv4.save ()

		ipv6 = IPAddress (
			address = "2a03:2260:2342:ffff::%s/128" % node_id,
			interface = bbr_lo_iface
		)
		ipv6.save ()

		bbr.primary_ip4 = ipv4
		bbr.primary_ip6 = ipv6
		bbr.save ()
		self.log_success ("Configured %s + %s on lo interface of %s" % (ipv4, ipv6, bbr))



	def run (self, data, commit):
		site = data['site']
		site_no = data['site_no']

		rack_name = data['rack_name']
		rack_units = data['rack_units']

		panel_ports = data['panel_ports']

		pole_setup = data['pole_setup']

		sw_asset_tag = data['sw_asset_tag']

		bbr_asset_tag = data['bbr_asset_tag']
		node_id = data['node_id']


		# Set up POP Mgmt VLAN
		vlan = self.create_mgmt_vlan (site, site_no)

		# Mgmt prefix
		prefix = self.create_mgmt_prefix (site, site_no, vlan)

		# Create rack
		rack = self.create_rack (site, rack_name, rack_units)

		# Create patch panel
		pp = self.create_patch_panel (site, rack, rack_name, panel_ports)

		self.create_and_connect_surges (site, rack, pp, pole_setup)

		# Create switch
		sw = self.setup_swtich (site, rack, pp, panel_ports, vlan, site_no, sw_asset_tag)

		# Create backbone router
		bbr = self.setup_bbr (site, rack, vlan, site_no, node_id, bbr_asset_tag, sw)
