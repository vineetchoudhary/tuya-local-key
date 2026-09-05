#!/usr/bin/env python3
"""
Fake 60-device account the README screenshots are taken against.
"""

import datetime as dt

from tuya_sharing.device import CustomerDevice, DeviceFunction, DeviceStatusRange

ROOMS = ["Living Room", "Bedroom", "Kitchen", "Hallway", "Office",
         "Balcony", "Garage", "Dining Room", "Guest Room", "Porch"]
KINDS = [("Lamp", "Wi-Fi Smart Bulb"), ("Plug", "Energy Monitoring Plug"),
         ("Strip", "RGB Light Strip"), ("Motion Sensor", "PIR Motion Sensor"),
         ("Switch", "Wall Switch"), ("Fan", "Ceiling Fan")]

# Device 01 is the plug verbatim; every later device steps these forward.
DEVICE_ID = "d7ddc303490bb07ca5rqmj"    # base 36 in [9:14], fixed head and tail
PRODUCT_ID = "ofxioj0ypuygidrs"         # [12:] becomes a hex counter from 02 on
UUID_HEAD, UUID_START = "4e1ea52584f6", 0xa774
ICON = "smart/icon/ay153354689696{digit}rWFJ/{slug}.png"
STEP = 7919                             # prime, so the ids look unrelated
PRODUCT_ID_START, PRODUCT_ID_STEP = 0x1000, 41

# Local keys are decorative, but they have to look like keys: two digits and
# two punctuation marks moving at different rates through a fixed run.
SYMBOLS_MID = "+?;#@&=%:./"
SYMBOLS_END = "+;&=%:./#@?"

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
# Device 01 reported last; each later device is another half hour stale.
UPDATED = int(dt.datetime(2026, 8, 4, 9, 0, tzinfo=IST).timestamp())
UPDATE_STEP = 1800
FIRST_PAIRED = int(dt.datetime(2026, 1, 4, 9, 0, tzinfo=IST).timestamp())
LAST_PAIRED = int(dt.datetime(2026, 7, 30, 6, 30, tzinfo=IST).timestamp())

OFFLINE_EVERY = 7       # n % 7 == 4 is offline: 9 of the 60
OFFLINE_AT = 4

WATTS = '{"unit":"W","min":0,"max":50000,"scale":1,"step":1}'
SECONDS = '{"unit":"s","min":0,"max":86400,"scale":0,"step":1}'
MILLIAMPS = '{"unit":"mA","min":0,"max":30000,"scale":0,"step":1}'
VOLTS = '{"unit":"V","min":0,"max":5000,"scale":1,"step":1}'
KILOWATT_HOURS = '{"unit":"kWh","min":0,"max":50000,"scale":3,"step":1}'

BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(value, width):
    out = ""
    while value:
        value, digit = divmod(value, 36)
        out = BASE36[digit] + out
    return out.rjust(width, "0")


def device_id(n):
    head, slice_, tail = DEVICE_ID[:9], DEVICE_ID[9:14], DEVICE_ID[14:]
    return head + _base36(int(slice_, 36) + (n - 1) * STEP, len(slice_)) + tail


def local_key(n):
    i = n - 1
    return (f"{(5 - i) % 10}vps{SYMBOLS_MID[i % 11]}n{(4 + 3 * i) % 10}"
            f"FwxR{i % 10}?df{SYMBOLS_END[i % 11]}")


def device(n):
    """The nth device, 1-based."""
    kind, product_name = KINDS[(n - 1) % len(KINDS)]
    id_ = device_id(n)
    d = CustomerDevice(
        name=f"{ROOMS[(n - 1) % len(ROOMS)]} {kind} {n:02d}",
        id=id_,
        uuid=UUID_HEAD + format((UUID_START + (n - 1) * STEP) % 0x10000, "04x"),
        local_key=local_key(n),
        product_id=(PRODUCT_ID if n == 1 else PRODUCT_ID[:12] +
                    format(PRODUCT_ID_START + (n - 1) * PRODUCT_ID_STEP, "x")),
        product_name=product_name,
        category="cz",
        model="SP20-EU",
        icon=ICON.format(digit=(n - 1) % 10, slug=id_[9:15]),
        ip=f"192.168.1.{9 + n}",
        lat="12.9716",
        lon="77.5946",
        time_zone="+05:30",
        online=n % OFFLINE_EVERY != OFFLINE_AT,
        sub=False,
        uid="eu1652374915283hgKd",
        owner_id="21489372",
        asset_id="",
        biz_type=18,
        create_time=FIRST_PAIRED,
        active_time=LAST_PAIRED,
        update_time=UPDATED - (n - 1) * UPDATE_STEP,
    )
    d.set_up = True
    d.support_local = True
    d.status = {"switch_1": True, "countdown_1": 0, "cur_power": 812,
                "cur_current": 3520, "cur_voltage": 2361, "add_ele": 1284}
    d.function = {
        "switch_1": DeviceFunction(code="switch_1", name="Switch",
                                   desc="Master switch", type="Boolean", values="{}"),
        "countdown_1": DeviceFunction(code="countdown_1", name="Countdown",
                                      desc="Auto-off timer", type="Integer",
                                      values=SECONDS),
    }
    d.status_range = {
        "switch_1": DeviceStatusRange(code="switch_1", type="Boolean", values="{}"),
        "countdown_1": DeviceStatusRange(code="countdown_1", type="Integer",
                                         values=SECONDS),
        "cur_power": DeviceStatusRange(code="cur_power", type="Integer",
                                       values=WATTS, report_type="minux"),
        "cur_current": DeviceStatusRange(code="cur_current", type="Integer",
                                         values=MILLIAMPS),
        "cur_voltage": DeviceStatusRange(code="cur_voltage", type="Integer",
                                         values=VOLTS, report_type=""),
        "add_ele": DeviceStatusRange(code="add_ele", type="Integer",
                                     values=KILOWATT_HOURS, report_type="sum"),
    }
    d.local_strategy = {
        1: {"value_convert": "raw", "status_code": "switch_1", "config_item": {}},
        9: {"value_convert": "raw", "status_code": "countdown_1", "config_item": {}},
        19: {"value_convert": "raw", "status_code": "cur_power", "config_item": {}},
        20: {"value_convert": "raw", "status_code": "cur_current", "config_item": {}},
        21: {"value_convert": "raw", "status_code": "cur_voltage", "config_item": {}},
        22: {"value_convert": "raw", "status_code": "add_ele", "config_item": {}},
    }
    return d


def fleet(count=60):
    return [device(n) for n in range(1, count + 1)]
