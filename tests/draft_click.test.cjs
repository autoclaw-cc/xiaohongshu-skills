const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const source = fs.readFileSync(
  __dirname + "/../extension/draft_click.js",
  "utf8",
);
const text = (value, id) => ({
  nodeType: 3,
  nodeValue: value,
  nodeId: id,
  backendNodeId: id,
});
const button = (label, id) => ({
  nodeName: "BUTTON",
  nodeId: id,
  backendNodeId: id,
  children: [text(label, id + 100)],
});
async function run({
  width = 1200,
  height = 800,
  x = 400,
  y = 730,
  title = "test",
  covered = false,
  disabled = false,
  duplicate = false,
  legacy = false,
  missing = false,
  buttonDisabled = false,
  wrongHost = false,
  childHit = false,
  wrongDomain = false,
  invalidBox = false,
  shadowLegacy = false,
} = {}) {
  const events = [];
  const save = button("暂存离开", 3),
    publish = button("发布", 4);
  if (buttonDisabled) save.attributes = ["disabled", ""];
  const host = {
    nodeName: "XHS-PUBLISH-BTN",
    attributes: [
      "is-save-draft",
      "true",
      "save-text",
      "暂存离开",
      "save-disabled",
      String(disabled),
    ],
    shadowRoots: [
      { nodeName: "#document-fragment", children: [save, publish] },
    ],
  };
  if (wrongHost) host.attributes = ["is-save-draft", "false"];
  if (legacy || shadowLegacy)
    save.attributes = [...(save.attributes || []), "class", "custom-button"];
  const roots = missing
    ? []
    : legacy
      ? [save]
      : duplicate
        ? [
            host,
            { ...host, shadowRoots: [{ children: [button("暂存离开", 8)] }] },
          ]
        : [host];
  const context = vm.createContext({
    URL,
    Number,
    chrome: {
      debugger: {
        attach: async () => {},
        detach: async () => {},
        sendCommand: async (_, method, params) => {
          if (method === "Runtime.evaluate")
            return {
              result: { value: JSON.stringify({ title, width, height }) },
            };
          if (method === "DOM.getDocument")
            return { root: { children: roots } };
          if (method === "DOM.getBoxModel")
            return {
              model: {
                border: invalidBox
                  ? [NaN, y, x + 120, y, x + 120, y + 40, x, y + 40]
                  : [x, y, x + 120, y, x + 120, y + 40, x, y + 40],
              },
            };
          if (method === "DOM.getNodeForLocation")
            return { backendNodeId: covered ? 999 : childHit ? 103 : 3 };
          if (method === "DOM.describeNode")
            return { node: { backendNodeId: params.backendNodeId } };
          if (method === "Input.dispatchMouseEvent") {
            events.push(params);
            return {};
          }
          throw Error(method);
        },
      },
    },
    getOrOpenXhsTab: async () => ({
      id: 7,
      url: wrongDomain
        ? "https://www.xiaohongshu.com/"
        : "https://creator.xiaohongshu.com/publish/publish",
    }),
  });
  vm.runInContext(source, context);
  let error;
  try {
    await context.cmdDraftClick({ expectedTitle: "test" });
  } catch (e) {
    error = e;
  }
  return { events, error };
}
(async () => {
  for (const layout of [
    { width: 1200, height: 800, x: 400, y: 730 },
    { width: 1920, height: 958, x: 738, y: 893 },
    { width: 900, height: 700, x: 310, y: 620 },
  ]) {
    const { events, error } = await run(layout);
    assert.equal(error, undefined);
    assert.equal(events.length, 3);
    assert.equal(events[1].x, layout.x + 60);
    assert.equal(events[1].y, layout.y + 20);
    assert.equal(events[1].type, "mousePressed");
  }
  for (const bad of [
    { title: "other" },
    { covered: true },
    { disabled: true },
    { x: 2000 },
    { duplicate: true },
    { missing: true },
    { buttonDisabled: true },
    { wrongHost: true },
    { wrongDomain: true },
    { invalidBox: true },
    { width: 0 },
    { height: 0 },
    { disabled: true, shadowLegacy: true },
  ]) {
    const { events, error } = await run(bad);
    assert.ok(error);
    assert.equal(events.length, 0);
  }
  for (const good of [{ legacy: true }, { childHit: true }]) {
    const { events, error } = await run(good);
    assert.equal(error, undefined);
    assert.equal(events.length, 3);
  }
  console.log(
    "PASS: 3 layouts, legacy button, child hit; 13 unsafe states refuse clicks",
  );
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
