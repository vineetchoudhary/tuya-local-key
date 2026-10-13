# Network Scanning

Local-control tools such as tinytuya, tuya-local and localtuya need each device's protocol version as well as its local key. Tuya's device-sharing API doesn't return one, and the IP address it reports is your WAN address, so both have to come from the device itself.

Click **Scan network**, enter your IoT VLAN or subnet, the one your Tuya devices are on (for example `192.168.2.0/24`, or several separated by commas, up to 1,024 addresses), and start the scan. The app then:

1. Opens a plain TCP connection to port 6668 on every address, to find the ones listening.
2. Asks each of those addresses for a device's status with that device's local key, trying 3.3, 3.4 and 3.5 in turn, then 3.1. A device only answers to its own key, so a reply identifies it.

A first scan of a /24 takes about 30 seconds for a typical home. An account with many devices takes longer, up to a few minutes, because each address that answers is asked with every key not yet matched. The results are saved, so later visits show them straight away. The next scan checks each device at its last address first, so a device that hasn't moved is found in about a second, but the scan still starts by checking every address, which takes several seconds on a /24. A scan never runs on its own: Refresh only reloads the list from Tuya.

For a device the scan missed, open its details panel and use **Check** with its IP address (for example `192.168.2.1`), which you can find in your router's client list. A Check that fails at an address the device wasn't known at only reports the result. It doesn't replace what the last scan saved.

### Networks and Firewalls

The scan makes ordinary routed TCP connections and never relies on broadcasts, so it works when your devices are on a different VLAN from the app. Allow TCP port 6668 from the machine running Tuya Local Key to the IoT network. From Docker or the Home Assistant app, connections leave with the host's address, so the rule is for the host. The IoT network needs no access back. Set `LAN_SUBNET` to prefill the scan box.

### What the Status Column Shows

After a scan, the Status column shows what the device did when asked, tagged `LAN`. Devices without a scan result still show the cloud's online flag, which is often wrong about devices that are on your network.

| Status | Meaning |
|---|---|
| reachable | Answered on port 6668 to its local key. |
| via gateway | A Zigbee or Bluetooth sub-device. It shares its gateway's key and is reached at the gateway's IP and version. It has no protocol version of its own, so its Protocol column shows a dash with the gateway's version in a tooltip. Its details panel labels the values **Gateway IP** and **Gateway protocol**, and links to the gateway when known. It is greyed out when the scan didn't find the gateway. See [Gateways](#gateways). |
| offline | A sub-device whose gateway answered, but reports it offline. The scan asks each gateway it finds which of its sub-devices are online, since the gateway answering says nothing about them. |
| check needed | A gateway Tuya lists without a key, which the last scan found but couldn't tell apart from your other gateways. Check it at its IP. See [Gateways](#gateways). |
| busy | Refused the connection at its last known IP. Tuya devices accept only one local connection, so a device already connected to Home Assistant or another local client refuses new ones. A manual Check will be refused too until that client lets go. |
| key mismatch | Answered at its last known IP, but not to its key, at the version it used before, twice in a row. The key has probably changed, so click Refresh. Another device may also have taken that IP. |
| unreachable | Didn't answer at its last known IP. |
| not found | No scanned address answered to its key. |

The scan never holds a connection itself. Each check opens a connection, asks once and closes it, usually within a fraction of a second, or after a few seconds for an address that doesn't answer. It never has more than one connection open to an address, so a local integration that reconnects at that exact moment only has to retry, as it does after any dropped connection.

The scan summary lists the addresses that refused connections, the ones that answered but not to any key in your account, and any that ran out of time before every key was tried, so you can match them against your router's client list. An address that refuses isn't necessarily a Tuya device: any device that doesn't use port 6668 refuses it. The .1 that starts each subnet you enter, where a router usually sits, is still scanned but left out of these lists unless one of your devices answers there.

### Gateways

Tuya's device-sharing API can list a Zigbee or Bluetooth gateway with no local key, and put the gateway's key on each of its sub-devices instead. It doesn't say which sub-devices belong to which gateway, and it can mark the gateway itself as a sub-device. The scan tells gateways apart by their category, and tries the keys on your sub-devices at each address that answers.

When Tuya or a scan identifies which gateway a sub-device belongs to, the table groups it under that gateway. Groups start expanded. Use the arrow beside the gateway's name to collapse or expand its sub-devices. Sorting keeps each group together and sorts its sub-devices within it. Searching reveals matching sub-devices and shows their gateway for context. Unlinked sub-devices stay in their own rows until the gateway is known.

When the account has sub-devices, the key column is labelled **Local Key / Node ID**. Gateway and direct-device rows show local keys; sub-device rows show copyable Node IDs with setup tooltips. Node IDs stay visible when keys are hidden, and show a dash when missing. Without sub-devices, the heading remains **Local Key**. The details panel keeps **Gateway local key**, **Node ID** and **UUID** separate for sub-devices.

- **One gateway without a key:** it gets the key its sub-devices carry. Its row shows that key, marked with an asterisk, along with its IP and version. The asterisk's tooltip and a note below the table say the key comes from a sub-device.
- **Several gateways without a key:** the scan still finds where each key answers, so every sub-device gets its gateway's IP and version. Tuya's data doesn't say which gateway is at which address, so the gateways show **check needed**, and the scan summary lists each address with the sub-devices whose key answered there. Open a gateway and **Check** it at its IP, with one click on the address its sub-devices point to, or one from your router's client list. The key it answers to becomes its key, and later scans remember it. When that leaves only one gateway, it's checked too, so two gateways take one click. A gateway checked at the wrong IP takes the other gateway's key, so check it again at the right one.

For tuya-local setup, use the gateway's IP, protocol version and local key, together with the sub-device's **Node ID**. These fields are available in the sub-device's details panel, with a link to its gateway when known.

In the CSV export and the CLI, a sub-device keeps its gateway's IP in `local_ip` and version in `protocol_version`, which local tools need to reach it, and `lan_gateway_id` names that gateway. `lan_sub_online` says whether that gateway reports it online, and is empty when the gateway didn't say. Collapsing a group does not remove its sub-devices from the CSV. The export still follows the filter and sort order, and excludes gateways shown only for context.

### device22

Some devices need a quirk tinytuya calls `device22`. The Protocol column marks it next to the version, and the details panel gives the name tuya-local uses: `3.22` for 3.3 with the quirk, `3.42` for 3.4. Version 3.2 behaves exactly like 3.3 with the quirk, so the scan reports it that way.
