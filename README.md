# Max' NetBox Scripts

This repository contains some
[NetBox](https://docs.netbox.dev/en/stable/)
[Custom Scripts](https://docs.netbox.dev/en/stable/customization/custom-scripts/)
I wrote to make life of operators easier.

Currently the two scripts here were written for the [Freifunk Hochstift](https://www.ffho.net) network,
and are used to provision NetBox data to be consumed by the [FFHO Salt stack](https://github.com/FreifunkHochstift/ffho-salt-public).

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

The 2nd script provisions Wireguard tunnels in NetBox between two nodes, allowing each side
to be a Device or a VM.  To model the connection between two nodes custom fields (type object)
on interfaces are used which will be set to the remote Device or VM.  IP allocation happens
programatically so that the only input to the script are server + client Device or VM.

The only prerequisites for the script to work are the custom fields and Wireguard keys configured
on the peers with config context.
