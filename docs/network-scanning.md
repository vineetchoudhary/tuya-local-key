# Network Scanning

Local integrations need a device's local IP, protocol version, and local key. Scanning finds the IP and protocol on your network.

Choose **Scan network**, enter your IoT subnet, such as `192.168.2.0/24`, and start the scan. Separate multiple subnets with commas, up to 1,024 addresses in total. A typical /24 scan takes about 30 seconds, while larger device lists can take a few minutes.

Results are saved for later visits. Scans run only when requested. **Refresh** fetches device details from Tuya without scanning the network.

For a missed device, open **Local network** and check the IP shown in your router's client list. Use **Recheck connection** to test an existing connection.

### Fix Connection

Open a device's **Local network > Fix connection** for guided troubleshooting:

1. Review the saved details.
2. Enter the device's local IP and test it, or scan to find the address.
3. Follow the suggested recovery step or open the setup values after a successful check.

Use **Back** or Escape to return to the previous step. Changes to your local integration must be made in that integration.

To read the device's data points, use [Current values](usage.md#read-current-values).

### Networks and Firewalls

Allow TCP port **6668** from the machine running Tuya Local Key to the IoT network. Scanning works across routed VLANs without broadcasts. For Docker and Home Assistant, allow traffic from the host. Set `LAN_SUBNET` to prefill the scan range.

### What the Status Column Shows

Local results are tagged **LAN** to distinguish them from Tuya's cloud status.

| Status | Meaning |
|---|---|
| reachable | The device answered with its local key. |
| via gateway | A sub-device reached through its gateway. See [Gateways](#gateways). |
| offline | The gateway reports that its sub-device is offline. |
| check needed | A gateway needs to be identified at its correct IP. |
| busy | Another local client may be using the connection. Disconnect that client and retry. |
| key mismatch | The device rejected the saved key. Refresh from Tuya and confirm the IP. |
| unreachable | The device did not answer at its last known IP. |
| not found | The scan did not find a matching device. |

Protocol 3.1 replies show **Device found**, because they cannot verify the local key. A successful gateway check does not prove that its sub-devices are responding.

Checks close their connections after reading status. The scan summary lists refused, unmatched, and incomplete checks to help compare results with your router's client list.

### Gateways

Zigbee and Bluetooth sub-devices use their gateway's local key, IP, and protocol. Their own **Node ID** identifies them to the gateway. Copy these settings from the sub-device's **Set up in…** page.

Identified sub-devices appear under their gateway in the table. The **Local Key / Node ID** column shows local keys for gateways and Node IDs for sub-devices.

If only one gateway lacks a key, the app can use the key supplied with its sub-devices. With several unidentified gateways, open each gateway's **Local network** page and check its correct IP from your router. Avoid guessing addresses, as that can associate a gateway with the wrong key.

### device22

Some devices use tinytuya's `device22` quirk. The app shows the corresponding tuya-local version in the device details: **3.22** for protocol 3.3 with the quirk, or **3.42** for 3.4. Protocol 3.2 is reported as 3.3 with this quirk.
