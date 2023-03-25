#!/usr/bin/python3
#
# Maximilian Wilhelm <max@sdn.clinic>
#  --  Mon 30 Jan 2023 11:09:11 PM CET
#

from django.utils.text import slugify

from dcim.choices import LinkStatusChoices
from dcim.models import Cable, Device, RearPort
from extras.scripts import *

try:
	from utilities.exceptions import AbortScript
except ModuleNotFound:
	class AbortScript(Exception):
		pass


class ConnectRearPorts(Script):
    class Meta:
        description = "Connect Rear ports of two devices"

    device_a = ObjectVar(
        description = "Device on A end",
        model=Device,
    )
    device_b = ObjectVar(
        description = "Device on B end",
        model=Device,
    )
    connected = BooleanVar(
        description = "Mark the cables as connected instead of planned (default)",
    )

    commit_default = True

    def run(self, data, commit):
        dev_a = data["device_a"]
        dev_b = data["device_b"]
        connected = data["connected"]

        a_rps = RearPort.objects.filter(
            device_id = dev_a.id
        )
        b_rps = RearPort.objects.filter(
            device_id = dev_b.id
        )

        a_rps_len = len(a_rps)
        b_rps_len = len(b_rps)
        if a_rps_len != b_rps_len:
            raise AbortScript(f"Devicess have different number of rear ports: {a_rps_len} vs. {b_rps_len}")

        # Validate compability of port types? copper vs fiber?

        # planned or connected?
        cables_status = LinkStatusChoices.STATUS_PLANNED
        if connected:
            cables_status = LinkStatusChoices.STATUS_CONNECTED

        for i in range(a_rps_len):
            rp_a = a_rps[i]
            rp_b = b_rps[i]

            # check if connected to B
            if a_rps[i].link:
                self.log_info(f"Rear port {rp_a} already connected, skipping.")
                continue

            c = Cable(
                a_terminations = [ rp_a ],
                b_terminations = [ rp_b ],
                status = cables_status
            )
            c.save()
            self.log_success(f"Connected rear port {rp_a} to {rp_b}.")
