// Cross-feature data. Controllers keep their own timers and caches.
const appState = {
  deviceData: [],
  filterText: "",
  devicesCachedAt: null,
  sortState: { key: null, dir: 1 },
  collapsedGateways: new Set(), // Normal browsing starts with gateways expanded.
  filteredCollapsedGateways: new Set(), // Searching keeps its own expansion state.
  tableKeysVisible: false,
  panelKeyVisible: false,
  selectedId: null,
  bulkSelectedIds: new Set(), // Selection is independent of the open details panel.
  selectionMode: false,
  excludedIds: new Set(),
  deviceView: "active",
  homes: null,
  selectedHome: "",
  preferredHome: "",
  roomsLoaded: false,
  selectedRoom: "",
  preferredRoom: "",
  selectedStatus: "",
  selectedCategory: "",
  exclusionsLoaded: false,
  exclusionRevision: -1,
  accountId: null,
  exclusionBusy: false,
  exclusionFailure: "",
  pendingExclusionSave: null,
  // Generation counters discard replies from older loads and account sessions.
  deviceLoadGeneration: 0,
  exclusionLoadGeneration: 0,
  accountGeneration: 0,
  panelView: "details",
  panelPage: null,
  panelHistory: [], // Contains UI state rather than copies of device records.
  deviceChanges: null,
  changeById: new Map(),
  snapshotReason: null,
  panelOpener: null,
  panelOpenerSelector: null,
  setupTool: "tuya-local",
  localTuyaEdition: "xzetsubou",
  setupAdvancedOpen: false,
  lanData: { results: {}, summary: null, changes: null, changes_at: null, targets: "", job: null },
  panelCheck: null,
  gatewayCategories: new Set(),
};

export { appState };
