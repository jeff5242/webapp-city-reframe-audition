# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a web application for Taipei City urban renewal audition (臺北市都市更新審議 webapp). The project supports the planning and review of urban renewal proposals under Taipei's Urban Renewal Office (都市更新處).

## Source Documents

`事業計劃報告書及權利變更計劃書.zip` contains the reference PDFs for this project:

- **1130902 1-事業計畫書** — Urban renewal business plan (事業計畫書) template, 111-year version
- **1130902 2-事業概要計畫書** — Business overview plan (事業概要計畫書) template
- **1131114 權利變換計畫書** — Rights exchange plan (權利變換計畫書) template (two copies: 113年 and 111年 version)

The applicable regulation is the **111年3月24日修正公布版** (Revised March 24, 2022), based on:
- 都市更新條例
- 臺北市都市更新自治條例
- 臺北市都市更新建築容積獎勵辦法

## Domain Knowledge

See `docs/urban-renewal-111-wiki.md` for a structured reference of the 111-year regulations, including the 14 major revisions, full business plan structure (18 chapters + 24 appendices), formatting requirements, and personal data masking rules.

## Key Domain Concepts

| Term | Meaning |
|------|---------|
| 事業計畫書 | Urban Renewal Business Plan — the main planning document |
| 事業概要計畫書 | Business Overview Plan — preliminary/summary plan |
| 權利變換計畫書 | Rights Exchange Plan — property rights redistribution plan |
| 更新單元 | Urban renewal unit — the geographic scope of a renewal project |
| 審議 | Deliberation/review by the Urban Renewal Review Committee |
| 實施者 | Implementer — the party executing the renewal project |
| 容積獎勵 | Floor area ratio bonus incentives |
| 公聽會 | Public hearing |
| 聽證 | Formal hearing |
| 幹事會 | Executive committee (pre-review stage) |
