// Draft-only click through closed Shadow DOM. All coordinates are CSS pixels from current layout.
function draftDescendants(node) {
  return [
    node,
    ...(node.children || []).flatMap(draftDescendants),
    ...(node.shadowRoots || []).flatMap(draftDescendants),
  ];
}
function draftLightDescendants(node) {
  return [node, ...(node.children || []).flatMap(draftLightDescendants)];
}
function draftAttr(node, name) {
  const attributes = node.attributes || [];
  for (let i = 0; i < attributes.length; i += 2) {
    if (attributes[i] === name) return attributes[i + 1];
  }
  return null;
}
function draftNodeText(node) {
  return draftDescendants(node)
    .filter((n) => n.nodeType === 3)
    .map((n) => n.nodeValue || "")
    .join("")
    .trim();
}
function draftBoxCenter(quad, viewport) {
  if (!Array.isArray(quad) || quad.length !== 8 || !quad.every(Number.isFinite))
    throw new Error("暂存按钮布局无效");
  if (
    !Number.isFinite(viewport.width) ||
    !Number.isFinite(viewport.height) ||
    viewport.width <= 0 ||
    viewport.height <= 0
  )
    throw new Error("视口尺寸无效");
  const x = (quad[0] + quad[2] + quad[4] + quad[6]) / 4;
  const y = (quad[1] + quad[3] + quad[5] + quad[7]) / 4;
  const area = Math.abs(
    (quad[2] - quad[0]) * (quad[5] - quad[1]) -
      (quad[3] - quad[1]) * (quad[4] - quad[0]),
  );
  if (area < 1 || x < 0 || y < 0 || x >= viewport.width || y >= viewport.height)
    throw new Error("暂存按钮不在视口内或不可见");
  return { x, y };
}
async function draftRealPoint(target, point) {
  await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
    type: "mouseMoved",
    x: point.x,
    y: point.y,
  });
  await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
    type: "mousePressed",
    x: point.x,
    y: point.y,
    button: "left",
    buttons: 1,
    clickCount: 1,
  });
  await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
    type: "mouseReleased",
    x: point.x,
    y: point.y,
    button: "left",
    buttons: 0,
    clickCount: 1,
  });
}
async function cmdDraftClick({ expectedTitle } = {}) {
  const tab = await getOrOpenXhsTab();
  if (new URL(tab.url).hostname !== "creator.xiaohongshu.com")
    throw new Error("暂存仅支持小红书创作平台");
  const target = { tabId: tab.id };
  await chrome.debugger.attach(target, "1.3");
  try {
    const state = await chrome.debugger.sendCommand(
      target,
      "Runtime.evaluate",
      {
        expression: `JSON.stringify({title:document.querySelector('input[placeholder="填写标题会有更多赞哦"]')?.value,width:innerWidth,height:innerHeight})`,
        returnByValue: true,
      },
    );
    const viewport = JSON.parse(state.result.value);
    if (!expectedTitle || viewport.title !== expectedTitle)
      throw new Error("草稿标题已改变，停止点击");
    // CDP exposes closed shadow roots; read the actual button, not guessed offsets.
    const { root } = await chrome.debugger.sendCommand(
      target,
      "DOM.getDocument",
      { depth: -1, pierce: true },
    );
    const hosts = draftDescendants(root).filter(
      (n) =>
        n.nodeName === "XHS-PUBLISH-BTN" &&
        draftAttr(n, "is-save-draft") === "true" &&
        draftAttr(n, "save-text") === "暂存离开" &&
        draftAttr(n, "save-disabled") !== "true",
    );
    const shadowButtons = hosts.flatMap(draftDescendants);
    const legacyButtons = draftLightDescendants(root).filter(
      (n) =>
        n.nodeName === "BUTTON" &&
        (draftAttr(n, "class") || "").split(/\s+/).includes("custom-button"),
    );
    const candidates = [
      ...new Map(
        [...shadowButtons, ...legacyButtons].map((n) => [n.nodeId, n]),
      ).values(),
    ].filter(
      (n) =>
        n.nodeName === "BUTTON" &&
        draftNodeText(n) === "暂存离开" &&
        draftAttr(n, "disabled") === null,
    );
    const visible = [];
    for (const node of candidates) {
      try {
        const { model } = await chrome.debugger.sendCommand(
          target,
          "DOM.getBoxModel",
          { nodeId: node.nodeId },
        );
        visible.push({
          nodeId: node.nodeId,
          ...draftBoxCenter(model.border, viewport),
        });
      } catch (_) {
        /* No layout box or out of viewport. */
      }
    }
    if (visible.length !== 1)
      throw new Error(`无法唯一定位可见暂存按钮：找到 ${visible.length} 个`);
    const point = visible[0];
    const hit = await chrome.debugger.sendCommand(
      target,
      "DOM.getNodeForLocation",
      {
        x: Math.floor(point.x),
        y: Math.floor(point.y),
        includeUserAgentShadowDOM: true,
      },
    );
    const hitNode = await chrome.debugger.sendCommand(
      target,
      "DOM.describeNode",
      { backendNodeId: hit.backendNodeId, depth: 1 },
    );
    const buttonNodes = draftDescendants(
      candidates.find((n) => n.nodeId === point.nodeId),
    );
    if (
      !buttonNodes.some((n) => n.backendNodeId === hitNode.node.backendNodeId)
    )
      throw new Error("暂存按钮被遮挡，停止点击");
    await draftRealPoint(target, point);
    return { tabId: tab.id, x: point.x, y: point.y, title: viewport.title };
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
}
