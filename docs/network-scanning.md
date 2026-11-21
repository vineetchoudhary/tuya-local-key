# Network Scanning

Local integrations need a device's local IP, protocol version, and local key. A scan finds the IP and protocol.

Choose **Scan network**, enter your IoT VLAN or subnet, such as `192.168.2.0/24`, or device IPs, and choose **Start scan**. Separate entries with commas. Only private IPv4 addresses are accepted, up to 1,024 in total. A typical /24 scan takes about 30 seconds. Large device lists can take a few minutes.

Scans run only when you start one, and their results are saved. **Refresh** fetches device details from Tuya without scanning.

If a device is missed, open its **Local network** page and check the IP from your router's client list. Use **Recheck connection** to test a known connection.

## Fix Connection

Open a device's **Local network > Fix connection** for guided troubleshooting:

1. Review the saved details.
2. Enter the device's local IP and test it, or scan to find the address.
3. Follow the suggested recovery step or open the setup values after a successful check.

Use **Back** or Escape to return to the previous step. Changes to your local integration must be made in that integration.

To read the device's data points, use [Current values](usage.md#read-current-values).

## Networks and Firewalls

Allow TCP port **6668** from the machine running Tuya Local Key to the IoT network. Scanning works across routed VLANs without broadcasts. For Docker and Home Assistant, allow traffic from the host. Set `LAN_SUBNET` to prefill the scan range.

## What the Status Column Shows

Local results are tagged **LAN** to distinguish them from Tuya's cloud status.

| Status | Meaning |
|---|---|
| reachable | The device answered with its local key. |
| via gateway | A sub-device reached through its gateway. Greyed out when the last scan did not find the gateway. See [Gateways](#gateways). |
| offline | The gateway reports that its sub-device is offline. |
| check needed | A gateway needs to be identified at its correct IP. |
| busy | The device refused the connection. Another local client may be connected, or the IP changed. Disconnect that client and retry. |
| key mismatch | The device rejected the saved key. Refresh from Tuya and confirm the IP. |
| unreachable | The device did not answer at its last known IP. |
| not found | The scan did not find a matching device. |

With protocol 3.1, **Local network** shows **Device found**, because 3.1 replies cannot verify the local key. A successful gateway check does not prove that its sub-devices are responding.

Checks close their connections after reading status. The scan summary lists refused, unmatched, and incomplete checks to help compare results with your router's client list.

## Gateways

Zigbee and Bluetooth sub-devices use their gateway's local key, IP, and protocol. Their own **Node ID** identifies them to the gateway. Copy these settings from the sub-device's **Set up in…** page.

Identified sub-devices appear under their gateway in the table. The **Local Key / Node ID** column shows local keys for gateways and Node IDs for sub-devices.

If only one gateway lacks a key, the app can use the key supplied with its sub-devices. With several unidentified gateways, open each gateway's **Local network** page and check its correct IP from your router. Avoid guessing addresses, as that can associate a gateway with the wrong key.

## device22

Some devices use tinytuya's `device22` quirk. The device details and tuya-local setup show the matching tuya-local version: **3.22**, **3.42**, or **3.52** for protocol 3.3, 3.4, or 3.5 with the quirk. Protocol 3.2 is reported as 3.3 with the quirk.
