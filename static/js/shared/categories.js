// Common Tuya category names. Unlisted codes remain individually selectable.
// https://developer.tuya.com/en/docs/iot/standarddescription?id=K9i5ql6waswzq
// https://developer.tuya.com/en/docs/iot/gateway-and-other?id=Kaiuz4dv3ohz6
const CATEGORY_LABELS = new Map(Object.entries({
  bh: "Smart kettle", bx: "Refrigerator", cjkg: "Scene switch",
  ckmkzq: "Garage door opener", ckqdkg: "Card switch", cl: "Curtain",
  clkg: "Curtain switch", co2bj: "CO2 detector", cobj: "CO detector",
  cs: "Dehumidifier", cwwsq: "Pet feeder", cwysj: "Pet fountain", cz: "Socket",
  dc: "String lights", dd: "Strip lights", dj: "Light", dlq: "Circuit breaker",
  dr: "Electric blanket", ds: "TV", fs: "Fan", fsd: "Ceiling fan light",
  fwd: "Ambiance light", ggq: "Irrigator", gyd: "Motion sensor light",
  hps: "Presence sensor", js: "Water purifier", jsq: "Humidifier",
  jtmspro: "Residential lock pro", kg: "Switch", kj: "Air purifier",
  kt: "Air conditioner", ktkzq: "Air conditioner controller", ldcg: "Light sensor",
  mal: "Alarm hub", mcs: "Contact sensor", ms: "Residential lock", pc: "Power strip",
  pir: "Motion sensor", "pm2.5": "PM2.5 detector", qn: "Heater", rqbj: "Gas alarm",
  rs: "Water heater", sd: "Robot vacuum", sgbj: "Siren alarm", sj: "Water leak detector",
  sos: "Emergency button", sp: "Camera", tgkg: "Dimmer switch", tgq: "Dimmer",
  wg2: "Gateway", wk: "Thermostat", wsdcg: "Temperature and humidity sensor",
  xdd: "Ceiling light", xfj: "Ventilation system", xxj: "Diffuser", xy: "Washing machine",
  ywbj: "Smoke alarm", zd: "Vibration sensor", zndb: "Electricity meter", znsb: "Water meter",
}));

function categoryOf(d) {
  return typeof d?.category === "string" ? d.category.trim() : "";
}

function categoryLabel(code) {
  const name = CATEGORY_LABELS.get(code);
  return name ? `${name} (${code})` : code;
}

export { categoryLabel, categoryOf };
