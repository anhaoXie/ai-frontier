# -*- coding: utf-8 -*-
"""ⓞ 任务定义层：这个工具到底追踪什么。

改这个文件 = 换任务。采集、评分、报告三处都读它，不写死在代码里。
"""
from __future__ import annotations

# ---------------------------------------------------------------- 领域定义

DOMAIN = {
    "id": "ai_frontier",
    "name": "AI / 大模型前沿",
    "goal": "每天把『过去 24 小时 AI 领域真正值得知道的新东西』找出来，"
            "分成论文/开源/产品/观点四类，给出可核对的事实和影响判断。",
    # 覆盖窗口
    "lookback_hours": 24,
}

# ---------------------------------------------------------------- 主题标签
# 只用于分组和筛选，不参与评分。命中越多的主题排在报告越前面。
TOPICS = {
    "模型与训练": [
        "pretrain", "pre-train", "training", "scaling law", "post-train",
        "rlhf", "rlvr", "dpo", "distillation", "distill", "fine-tun",
        "finetune", "mixture of experts", "moe", "architecture",
        "预训练", "训练", "微调", "蒸馏", "架构",
    ],
    "推理与效率": [
        "inference", "quantization", "quantiz", "int4", "int8", "fp8",
        "kv cache", "speculative", "decoding", "throughput", "latency",
        "flash attention", "compile", "kernel", "推理", "量化", "加速",
    ],
    "智能体与工具": [
        "agent", "agentic", "tool use", "function calling", "mcp",
        "computer use", "workflow", "orchestrat", "multi-agent",
        "智能体", "工具调用", "编排",
    ],
    "多模态": [
        "multimodal", "vision language", "vlm", "diffusion", "video",
        "speech", "audio", "image generation", "text-to-image",
        "多模态", "视觉", "语音", "视频生成", "文生图",
    ],
    "评测与安全": [
        "benchmark", "evaluation", "eval", "alignment", "safety",
        "jailbreak", "red team", "interpretab", "hallucinat",
        "评测", "对齐", "安全", "越狱", "可解释", "幻觉",
    ],
    "产品与商业": [
        "launch", "release", "api", "pricing", "funding", "acquisi",
        "partnership", "enterprise", "revenue", "发布", "上线", "融资",
        "收购", "定价", "合作",
    ],
    "开源生态": [
        "open source", "open-source", "open weights", "apache",
        "mit license", "llama", "qwen", "deepseek", "mistral",
        "开源", "开放权重",
    ],
}

# ---------------------------------------------------------------- 显著性词典
# 出现在标题/摘要里 = 这条大概率值得看。权重设计原则：
# 越具体、越难伪造的信号权重越高；"ai" 这种词几乎不给分。
SALIENCE = {
    # 一线实验室与模型（出现即是大事）
    "gpt-5": 3.0, "gpt5": 3.0, "gpt-6": 3.0,
    "claude": 2.0, "gemini": 2.0, "llama": 1.6, "qwen": 1.6,
    "deepseek": 1.8, "mistral": 1.4, "grok": 1.6, "phi-": 1.2,
    "openai": 1.4, "anthropic": 1.4, "deepmind": 1.6, "meta ai": 1.2,
    # 具体技术里程碑
    "state of the art": 2.0, "sota": 1.6, "breakthrough": 1.6,
    "first ever": 1.4, "outperform": 1.5, "beats": 1.2,
    "open weights": 2.0, "open-source model": 1.8, "open source model": 1.8,
    "million-token": 1.8, "context window": 1.4, "long context": 1.4,
    "multimodal": 1.0, "reasoning model": 1.6, "test-time": 1.4,
    "mixture of experts": 1.4, "quantization": 1.0,
    "scaling law": 1.6, "emergent": 1.4,
    # 产业信号
    "acquisition": 1.4, "acquires": 1.4, "raises": 1.2, "valuation": 1.2,
    "regulation": 1.2, "lawsuit": 1.0, "ban": 1.2, "export control": 1.6,
    # 融资/政策类噪音降权
    "webinar": -2.0, "sponsored": -2.5, "podcast": -1.2, "newsletter": -1.0,
    "how to": -1.0, "tutorial": -0.8, "top 10": -1.5, "best ": -1.2,
    "deal of the day": -3.0, "coupon": -3.0, "hiring": -1.5,
}

# ---------------------------------------------------------------- 评分权重
# 这些数字决定"什么排前面"。改之前先想清楚你更在意什么：
#   w_salience  高 → 更看重"是不是具体的技术/产品信号"
#   w_heat      高 → 更看重社区热度（会偏向 HN/GitHub，arXiv 论文吃亏）
#   w_diversity 高 → 更看重"多个独立来源都在说"（最能抵抗假消息，但会错过独家）
#   w_fresh     高 → 更看重"刚刚发生"
SCORING = {
    # 每个源的基准权重（0-1）：信息的一手程度
    "source_weight_default": 0.5,
    "w_salience": 0.38,   # 显著性：命中了多少具体信号
    "w_heat": 0.24,       # 热度：点数/星数/投票数
    "w_source": 0.18,     # 源权重：一手来源 > 二手转述
    "w_fresh": 0.12,      # 新鲜度
    "w_novelty": 0.06,    # 首次出现（相对过去 30 天）
    "w_diversity": 0.06,  # 独立印证：多个互相独立的来源团体
    "w_channel": 0.04,    # 渠道丰富度：同一生态内被多个渠道收录
    # 每日候选上限（控制人工阅读量）
    "max_candidates": 120,
    "min_score": 0.0,
}

# ---------------------------------------------------------------- 去重
DEDUP = {
    # 标题相似度阈值（0-1），超过视为同一条
    "title_similarity": 0.82,
    # 同一事件最多保留几条（跨源重复）
    "max_per_event": 3,
}
