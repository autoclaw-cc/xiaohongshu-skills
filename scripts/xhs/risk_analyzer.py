"""XHS NetLog 风控分析。

输入：netlogger 抓到的 entry 列表（dict 数组）
输出：结构化风控报告（dict）含 risk_level / detection_axes / high_risk_signals
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from typing import Any

# regex 兜底：reqBody 可能被截断导致 JSON.parse 失败，用正则直接抓字段
_RE_RISK_USER = re.compile(r'"isRiskUser"\s*:\s*"([^"]+)"')
_RE_RISK_REASON = re.compile(r'"isRiskReason"\s*:\s*"([^"]*)"')
_RE_MATCHED_PATH = re.compile(r'"matchedPath"\s*:\s*"([^"]+)"')
_RE_I_FIELDS = re.compile(r'"i1[234]"\s*:\s*(\d+)')


def analyze(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """从 netlog entries 反推 XHS 检测维度并给出风控结论。"""
    if not entries:
        return {
            "risk_level": "unknown",
            "total_requests": 0,
            "summary": "未采集到任何请求，无法分析",
            "detection_axes": {},
            "high_risk_signals": [],
            "warnings": ["netlog 为空，请确认 netlogger 已启用并执行过浏览/操作"],
        }

    # 按 category 分组
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        by_cat[e.get("category", "other")].append(e)

    # 按 host 分组
    by_host: Counter[str] = Counter(e.get("host", "") for e in entries)

    # 提前定义信号收集容器（后面 axes 提取过程中也会写）
    high_signals: list[str] = []
    warnings: list[str] = []

    # ── 检测维度提取 ──────────────────────────────────────────────
    axes: dict[str, Any] = {}

    # 浏览器指纹（shield/webprofile）
    shield_entries = [
        e for e in entries
        if e.get("path", "").startswith("/api/sec/v1/shield/webprofile")
    ]
    if shield_entries:
        first = shield_entries[0]
        sdk_version = ""
        platform = ""
        body = first.get("reqBody") or ""
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                sdk_version = parsed.get("sdkVersion", "")
                platform = parsed.get("platform", "")
        except Exception:
            pass
        axes["browser_fingerprint"] = {
            "host": first.get("host"),
            "endpoint": "/api/sec/v1/shield/webprofile",
            "sdk_version": sdk_version,
            "platform": platform,
            "called_count": len(shield_entries),
            "evidence": (
                f"Shield SDK {sdk_version} 在本会话上报浏览器指纹 {len(shield_entries)} 次，"
                "profileData 为加密 hex 串（具体指纹字段已被 XHS 服务端加密）"
            ),
        }

    # 行为埋点（t2 / collect）
    t2_entries = [
        e for e in entries
        if e.get("host") == "t2.xiaohongshu.com"
        and "/api/v2/collect" in e.get("path", "")
    ]
    if t2_entries:
        axes["behavior_tracking"] = {
            "host": "t2.xiaohongshu.com",
            "endpoint": "/api/v2/collect",
            "called_count": len(t2_entries),
            "protocol": "protobuf (base64)",
            "evidence": (
                f"行为采集端点被调用 {len(t2_entries)} 次，"
                f"占总流量 {len(t2_entries) * 100 // max(len(entries), 1)}%。"
                "请求体为 base64 编码的 protobuf 二进制"
            ),
        }

    # APM 监控（apm-fe）
    apm_entries = [e for e in entries if e.get("host") == "apm-fe.xiaohongshu.com"]
    if apm_entries:
        axes["apm_monitoring"] = {
            "host": "apm-fe.xiaohongshu.com",
            "endpoint": "/api/data",
            "called_count": len(apm_entries),
            "evidence": (
                f"APM SDK 上报 {len(apm_entries)} 条事件，包含 sdkSessionId / pageSessionId "
                "/ sdkSeqId 等会话追踪标识"
            ),
        }

    # 服务端风控判定（从 APM 上报的 measurement_data 反推 isRiskUser 字段）
    risk_judgments: list[dict[str, Any]] = []
    for e in apm_entries:
        body = e.get("reqBody") or ""
        if "isRiskUser" not in body:
            continue
        users = _RE_RISK_USER.findall(body)
        reasons = _RE_RISK_REASON.findall(body)
        paths = _RE_MATCHED_PATH.findall(body)
        for i, status in enumerate(users):
            risk_judgments.append({
                "ts": e.get("tsLabel"),
                "matchedPath": paths[i] if i < len(paths) else None,
                "isRiskUser": status,
                "isRiskReason": reasons[i] if i < len(reasons) else None,
            })
    if risk_judgments:
        user_states = Counter(r["isRiskUser"] for r in risk_judgments)
        non_pass = [r for r in risk_judgments if r["isRiskUser"] != "pass"]
        axes["server_risk_judgment"] = {
            "source": "apm-fe/api/data measurement_data.isRiskUser",
            "total_judgments": len(risk_judgments),
            "states": dict(user_states),
            "non_pass_count": len(non_pass),
            "evidence": (
                f"前端 SDK 在 {len(risk_judgments)} 个 API 调用后上报服务端风控判定结果到 APM。"
                f"状态分布：{dict(user_states)}。"
                + (f"⚠️ {len(non_pass)} 个非 pass 状态" if non_pass else "全部 pass，未被识别")
            ),
            "non_pass_samples": [
                {"ts": r["ts"], "path": r["matchedPath"], "state": r["isRiskUser"],
                 "reason": r["isRiskReason"]}
                for r in non_pass[:5]
            ],
        }
        # 任何非 pass 都是高风险信号
        for r in non_pass:
            high_signals.append(
                f"⚠️ isRiskUser={r['isRiskUser']} reason={r['isRiskReason']} "
                f"on {r['matchedPath']}"
            )

    # 业务 API 签名
    business_entries = [
        e for e in entries
        if e.get("host") == "edith.xiaohongshu.com" and e.get("reqFingerprint")
    ]
    if business_entries:
        xs_count = sum(1 for e in business_entries if e["reqFingerprint"].get("has_xs"))
        xt_count = sum(1 for e in business_entries if e["reqFingerprint"].get("has_xt"))
        xc_count = sum(1 for e in business_entries if e["reqFingerprint"].get("has_xsCommon"))
        axes["request_signature"] = {
            "scheme": (
                "x-s-common (current)" if xc_count > xs_count else "xs/xt (legacy)"
            ),
            "edith_total": len(business_entries),
            "has_xs": xs_count,
            "has_xt": xt_count,
            "has_xsCommon": xc_count,
            "coverage_pct": round(xc_count * 100 / len(business_entries), 1),
            "evidence": (
                f"业务 API {len(business_entries)} 条中，{xc_count} 条带 x-s-common，"
                f"{xs_count} 条带 xs，{xt_count} 条带 xt。"
                f"当前签名方案：{'x-s-common 单签名' if xc_count > xs_count else 'xs+xt 双签名（已过时）'}"
            ),
        }

    # Cookie 状态（取最新有 reqFingerprint 的 entry）
    fp_with_cookie = [e for e in entries if (e.get("reqFingerprint") or {}).get("cookie")]
    if fp_with_cookie:
        latest = fp_with_cookie[-1]["reqFingerprint"]["cookie"]
        axes["cookie_state"] = {
            "has_a1": latest.get("has_a1"),
            "has_web_session": latest.get("has_web_session"),
            "has_webId": latest.get("has_webId"),
            "has_gid": latest.get("has_gid"),
            "a1_preview": latest.get("a1_preview"),
            "web_session_preview": latest.get("web_session_preview"),
        }

    # ── 风险信号 ──────────────────────────────────────────────

    for e in entries:
        cat = e.get("category", "")
        status = e.get("status", 0)
        if cat == "signature_failure":
            high_signals.append(
                f"signature_failure: {e.get('path', '')[:80]} "
                f"error_code={e.get('errorCode')}"
            )
        elif cat == "risk_redirect":
            high_signals.append(
                f"risk_redirect: {e.get('path', '')[:60]} -> {e.get('redirectTo', '')[:60]}"
            )
        elif status == 999:
            high_signals.append(
                f"HTTP 999 (账号/IP 系统级封禁): {e.get('path', '')[:80]}"
            )
        elif status in (401, 403, 461):
            high_signals.append(f"HTTP {status}: {e.get('path', '')[:80]}")
        elif cat == "cookie_change":
            cookies = e.get("setCookie") or []
            if any("acw_tc" in c for c in cookies):
                high_signals.append(
                    f"acw_tc cookie 变更：阿里云 WAF 边缘风控可能触发 "
                    f"({e.get('path', '')[:60]})"
                )
            else:
                warnings.append(
                    f"cookie 变化: {','.join(cookies[:3])} ({e.get('path', '')[:60]})"
                )

    # 业务 API 签名覆盖率不足
    if "request_signature" in axes:
        cov = axes["request_signature"]["coverage_pct"]
        if cov < 80:
            warnings.append(
                f"x-s-common 签名覆盖率 {cov}% < 80%，"
                "部分业务 API 未签名（可能是公开端点）"
            )

    # 行为埋点缺失（自动化可能屏蔽了 t2/collect）
    if "behavior_tracking" not in axes and len(entries) > 20:
        warnings.append(
            "未检测到 t2.xiaohongshu.com/api/v2/collect 调用 —— "
            "XHS 期望此埋点存在，若被屏蔽反而异常，建议放开"
        )

    # ── 风险等级判断 ──────────────────────────────────────────
    if any("signature_failure" in s or "HTTP 999" in s for s in high_signals):
        risk_level = "high"
    elif any("HTTP 461" in s or "HTTP 403" in s or "HTTP 401" in s for s in high_signals):
        risk_level = "medium"
    elif any("acw_tc" in s or "risk_redirect" in s for s in high_signals):
        risk_level = "medium"
    elif warnings:
        risk_level = "low"
    else:
        risk_level = "safe"

    # ── summary ──────────────────────────────────────────
    summary_parts = [
        f"本会话采集 {len(entries)} 条请求",
        f"指纹上报 {len(shield_entries)} 次",
        f"行为埋点 {len(t2_entries)} 次",
    ]
    if high_signals:
        summary_parts.append(f"高风险信号 {len(high_signals)} 个")
    summary = "，".join(summary_parts) + "。"

    return {
        "risk_level": risk_level,
        "total_requests": len(entries),
        "summary": summary,
        "detection_axes": axes,
        "category_distribution": dict(Counter(e.get("category", "other") for e in entries)),
        "top_hosts": dict(by_host.most_common(10)),
        "high_risk_signals": high_signals,
        "warnings": warnings,
    }
