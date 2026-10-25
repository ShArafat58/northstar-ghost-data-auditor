# 🛡️ Northstar — Ghost Data Auditor

### Evidence-Driven AI Deletion Auditing System

**Because “Deletion Complete” doesn't always mean the data is gone.**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Hackathon](https://img.shields.io/badge/HackerRank-Orchestrate_2026-00A65A?style=for-the-badge)
![Tests](https://img.shields.io/badge/Tests-113%2F113_Passing-22A06B?style=for-the-badge)

---

## 🏆 The Competition

**HackerRank Orchestrate — October 2026**

**Challenge:** [Ghost Data Still Exists](https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists)

An AI engineering challenge focused on building an intelligent agent that can investigate, validate, and safely resolve incomplete personal-data deletion across interconnected systems.

## 🎯 The Problem

A system may report **“Deletion Complete”** while personal information still survives in databases, replicas, search indexes, support records, exports, or backups.

The challenge was to go beyond workflow status and determine:

- **Was deletion actually requested?**
- **Does personal data still exist anywhere?**
- **What actions and verification steps are required?**

Every decision needed to be backed by traceable evidence and aligned with data-retention policies.

## ⚙️ How I Solved It

Built **Northstar**, an evidence-grounded deletion audit pipeline with an integrated AI tool-calling agent.

The system combines historical identity resolution, SQL/JSON analysis, activity-log tracing, PDF/OCR extraction, and policy-aware decision-making to generate ordered audit and remediation plans.

A **Groq-powered AI agent (`openai/gpt-oss-20b`)** was also implemented and tested with real tool calls. The final full-dataset predictions use the validated deterministic evidence engine after A/B testing showed no additional accuracy from the model.

## 📊 Results & Performance

| Performance Metric | Verified Result |
|:---|---:|
| 🎯 Public Sample Status Accuracy | **100% — 32/32** |
| ⚡ Audit Step F1 Score | **90.7%** |
| 🔍 Audit Step Precision | **100%** |
| 🧩 Exact-Row Accuracy | **31.2% — 10/32** |
| 📂 Full Dataset Processed | **518/518 Requests** |
| 🛠️ Audit Steps Generated | **2,057** |
| 🤖 Real Groq Agent Executions | **32/32 Successful** |
| ✅ Automated Tests | **113/113 Passed** |

**Final validation:** 518 valid output rows, with evidence-backed audit steps and strict output-contract compliance.

> **Evaluation note:** Accuracy metrics are from the 32 publicly labeled sample cases. Hidden evaluation performance and final competition ranking have not been verified.

---

**Built for HackerRank Orchestrate 2026 | Python • AI Agents • Data Privacy • Evidence-Based Reasoning**
