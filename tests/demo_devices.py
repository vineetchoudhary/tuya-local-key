"""Fake tuya_sharing devices for the browser smoke tests.

Built with the real SDK classes so the serialization path under test is the same
one production uses: namespaces for the specs, int keys in local_strategy, and
timestamps as raw epochs. Every epoch is fixed, so the rendered dates are
assertable (UTC and Asia/Kolkata are given below each one).
"""

from tuya_sharing.device import CustomerDevice, DeviceFunction, DeviceStatusRange

# 2023-07-22 04:26:40 UTC = 09:56:40 IST
CREATE_TIME = 1_690_000_000
# 2024-07-03 09:46:40 UTC = 15:16:40 IST
ACTIVE_TIME = 1_720_000_000
# 2025-07-08 18:40:00 UTC = 2025-07-09 00:10:00 IST — deliberately a different
# local *date*, so a UTC-only rendering can't pass the timestamp assertions.
UPDATE_TIME = 1_752_000_000

INT_SPEC = '{"unit":"W","min":0,"max":50000,"scale":1,"step":1}'


def _plug():
    device = CustomerDevice(
        name="Kitchen Energy Plug",
        id="d7ddc303490bb07ca5rqmj",
        uuid="4e1ea52584f6a774",
        local_key="5vps+n4FwxR2?df;",
        product_id="ofxioj0ypuygidrs",
        product_name="Energy Monitoring Plug",
        category="cz",
        model="SP20-EU",
        icon="smart/icon/ay1533546896960rWFJ/1a2b3c.png",
        ip="192.168.1.61",
        lat="12.9716",
        lon="77.5946",
        time_zone="+05:30",
        online=True,
        sub=False,
        uid="eu1652374915283hgKd",
        owner_id="21489372",
        asset_id="",
        biz_type=18,
        create_time=CREATE_TIME,
        active_time=ACTIVE_TIME,
        update_time=UPDATE_TIME,
    )
    device.set_up = True
    device.support_local = True
    device.status = {"switch_1": True, "cur_power": 812}
    device.function = {
        "switch_1": DeviceFunction(code="switch_1", name="Switch", desc="Master switch",
                                   type="Boolean", values="{}"),
    }
    device.status_range = {
        "switch_1": DeviceStatusRange(code="switch_1", type="Boolean", values="{}"),
        "cur_power": DeviceStatusRange(code="cur_power", type="Integer", values=INT_SPEC,
                                       report_type="minux"),
    }
    device.local_strategy = {
        1: {"value_convert": "raw", "status_code": "switch_1", "config_item": {}},
        19: {"value_convert": "raw", "status_code": "cur_power", "config_item": {}},
    }
    return device


def _lamp():
    device = CustomerDevice(
        name="Living Room Lamp",
        id="bf9a1c2d3e4f5a6b7c8d9e",
        uuid="9f8e7d6c5b4a3210",
        local_key="Ab3!kQ9zPl2#mNvX",
        product_id="ay1533546896960rWFJ",
        product_name="Wi-Fi Smart Bulb",
        category="dj",
        ip="192.168.1.42",
        time_zone="+05:30",
        online=True,
        sub=False,
        create_time=CREATE_TIME,
        active_time=ACTIVE_TIME,
        update_time=UPDATE_TIME,
    )
    device.support_local = True
    device.status = {"work_mode": "white", "colour_data_v2": {"h": 240, "s": 800, "v": 900}}
    device.function = {
        "work_mode": DeviceFunction(code="work_mode", name="Mode", desc="Working mode",
                                    type="Enum", values='{"range":["white","colour","scene"]}'),
    }
    device.status_range = {
        "work_mode": DeviceStatusRange(code="work_mode", type="Enum",
                                       values='{"range":["white","colour","scene"]}'),
        "colour_data_v2": DeviceStatusRange(code="colour_data_v2", type="Json",
                                            values='{"h":{"min":0,"max":360}}'),
    }
    device.local_strategy = {21: {"value_convert": "raw", "status_code": "work_mode",
                                  "config_item": {}}}
    return device


def _sensor():
    device = CustomerDevice(
        name="Balcony Door Sensor",
        id="ebfa11223344556677",
        uuid="1122334455667788",
        local_key="Zz00Yy11Xx22Ww33",
        product_id="fkzctsfggjqpxsyd",
        product_name="Zigbee Contact Sensor",
        category="mcs",
        ip="",
        time_zone="+05:30",
        online=False,
        sub=True,
        node_id="a4c1380000112233",
        gateway_id="ebd8f1c0a1b2c3d4e5",
        # Undocumented by the SDK: proves unrecognised Tuya fields still surface.
        protocol_version="3.3",
        create_time=CREATE_TIME,
        active_time=ACTIVE_TIME,
        update_time=UPDATE_TIME,
    )
    device.support_local = False
    device.status = {"battery_percentage": 84}
    device.status_range = {
        "battery_percentage": DeviceStatusRange(
            code="battery_percentage", type="Integer",
            values='{"unit":"%","min":0,"max":100,"scale":0,"step":1}'),
    }
    return device


def _bare():
    # No timestamps and no specs: the panel has to degrade rather than break.
    return CustomerDevice(id="sparse000000000001", name="Unpaired Relay", online=False)


def devices():
    return [_plug(), _lamp(), _sensor(), _bare()]


PLUG, LAMP, SENSOR, BARE = 0, 1, 2, 3
