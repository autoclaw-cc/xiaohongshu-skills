"""图片生成工具 — 支持 Gemini Image Pro (Gemini 3 Pro Image (Nano Banana Pro)) 和 OpenAI gpt-image-1。

默认使用 Gemini 3 Pro Image (Nano Banana Pro) 以获取最佳效果。
回退到 OpenAI gpt-image-1（如果 Gemini API key 不可用）。

环境变量：
- GEMINI_API_KEY: Google AI Studio API key（优先使用）
- OPENAI_API_KEY: OpenAI API key（回退）
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# Gemini 3 Pro Image (Nano Banana Pro) — 最高质量
GEMINI_MODEL = "gemini-3-pro-image-preview"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# OpenAI 回退
OPENAI_MODEL = "gpt-image-1"
OPENAI_API_URL = "https://api.openai.com/v1/images/generations"


def generate_images(
    prompts: list[str],
    output_dir: str | Path = "/tmp/xhs/publish_content",
    prefix: str = "cover",
    size: str = "1024x1024",
) -> list[str]:
    """生成图片，优先使用 Gemini 3 Pro Image。

    Args:
        prompts: 图片描述列表。
        output_dir: 输出目录。
        prefix: 文件名前缀。
        size: 图片尺寸（仅 OpenAI 使用）。

    Returns:
        生成的图片文件路径列表。
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")

    if gemini_key:
        logger.info("使用 Gemini 3 Pro Image (Nano Banana Pro) 生成 %d 张图片", len(prompts))
        return _generate_gemini(prompts, output_dir, prefix, gemini_key)
    elif openai_key:
        logger.warning("Gemini API key 不可用，回退到 OpenAI gpt-image-1")
        return _generate_openai(prompts, output_dir, prefix, size, openai_key)
    else:
        raise RuntimeError("需要 GEMINI_API_KEY 或 OPENAI_API_KEY 环境变量")


def _generate_gemini(
    prompts: list[str],
    output_dir: Path,
    prefix: str,
    api_key: str,
) -> list[str]:
    """使用 Gemini 3 Pro Image (Nano Banana Pro) 生成图片。"""
    paths = []

    for i, prompt in enumerate(prompts):
        logger.info("生成图片 %d/%d (Gemini 3 Pro Image)...", i + 1, len(prompts))

        url = GEMINI_API_URL.format(model=GEMINI_MODEL) + f"?key={api_key}"

        req_body = json.dumps({
            "contents": [{
                "parts": [{"text": prompt}],
            }],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
            },
        }).encode()

        req = urllib.request.Request(
            url,
            data=req_body,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())

            # 提取图片数据
            img_data = None
            for candidate in result.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    if "inlineData" in part:
                        img_data = part["inlineData"].get("data", "")
                        mime = part["inlineData"].get("mimeType", "image/png")
                        break
                if img_data:
                    break

            if img_data:
                ext = "png" if "png" in mime else "jpg"
                path = output_dir / f"{prefix}_{i + 1}.{ext}"
                path.write_bytes(base64.b64decode(img_data))
                logger.info("  保存: %s (%d bytes)", path, path.stat().st_size)
                paths.append(str(path))
            else:
                logger.warning("  图片 %d 未返回图片数据", i + 1)

        except Exception as e:
            logger.error("  图片 %d 生成失败: %s", i + 1, e)

        # 速率限制
        if i < len(prompts) - 1:
            time.sleep(2)

    return paths


def _generate_openai(
    prompts: list[str],
    output_dir: Path,
    prefix: str,
    size: str,
    api_key: str,
) -> list[str]:
    """使用 OpenAI gpt-image-1 生成图片（回退方案）。"""
    paths = []

    for i, prompt in enumerate(prompts):
        logger.info("生成图片 %d/%d (OpenAI gpt-image-1)...", i + 1, len(prompts))

        req_body = json.dumps({
            "model": OPENAI_MODEL,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": "high",
        }).encode()

        req = urllib.request.Request(
            OPENAI_API_URL,
            data=req_body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())

            b64_data = result["data"][0].get("b64_json", "")
            if b64_data:
                img_bytes = base64.b64decode(b64_data)
            else:
                img_url = result["data"][0].get("url", "")
                with urllib.request.urlopen(img_url) as img_resp:
                    img_bytes = img_resp.read()

            path = output_dir / f"{prefix}_{i + 1}.png"
            path.write_bytes(img_bytes)
            logger.info("  保存: %s (%d bytes)", path, len(img_bytes))
            paths.append(str(path))

        except Exception as e:
            logger.error("  图片 %d 生成失败: %s", i + 1, e)

        if i < len(prompts) - 1:
            time.sleep(1)

    return paths
