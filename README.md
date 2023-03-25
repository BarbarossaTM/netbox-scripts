# Max' NetBox Scripts

This repository contains some
[NetBox](https://docs.netbox.dev/en/stable/)
[Custom Scripts](https://docs.netbox.dev/en/stable/customization/custom-scripts/)
I wrote to make life of operators easier.

Most scripts here were written for the [Freifunk Hochstift](https://www.ffho.net) network,
and are used to provision NetBox data to be consumed by the [FFHO Salt stack](https://github.com/FreifunkHochstift/ffho-salt-public).

## Connect Helper

This script gets two Devices as input and will connect all rear ports with a cable.  This is intended
to easy setting up a lot of patch panels with a lot of ports.  This might be extended in the future
with more bells and whistels, to be more clever and allow setting the kind of cable (CAT6, SMF, MMF, ...) etc.

## Provision Backbone POP

The ProvisionBackbonePOP script allows to fully provision a typical FFHO backbone POP, including
 * Mgmt prefix + VLAN
 * Rack
 * Patch panel including cabling to outdoor surge protectors (if any)
 * a switch, precabled to patch panel front ports, ports configured, and mgmt IP set
 * a backbone router, with ports configured, and loopback IP + mgmt VLAN/IP set
 * switch + backbone router also having asset tag and S/N set

See the screenshots in the [doc](ProvisionBackbonePOP/doc) folder for an example run.

## Wireguard tunnels

This script provisions Wireguard tunnels in NetBox between two nodes, allowing each side
to be a Device or a VM.  To model the connection between two nodes custom fields (type object)
on interfaces are used which will be set to the remote Device or VM.  IP allocation happens
programatically so that the only input to the script are server + client Device or VM.

The only prerequisites for the script to work are the custom fields and Wireguard keys configured
on the peers with config context.
