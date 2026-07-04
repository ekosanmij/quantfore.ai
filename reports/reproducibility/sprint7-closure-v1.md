# Sprint 7 Reproducibility and Closure

`claims_eligible=false`

- Decision: `pass`
- Clean commit: `72eeffbdab02b6a1350b516b2bee5e549a9896e0`
- Bundle manifest: `8b39fe268b3414495f7a2f95fe00e7b76f4afc1f33cec961ef095f4495a90a6e`
- Price exclusions: `78da4b5509122b336597441d06df388c9fdec5ff9618a6785f291fcfe698fbba`
- Minimum full-universe price coverage: `0.962451`
- Universe: `sp500-pit-v1`
- Backtest window: `2017-01-01` through `2025-06-30`
- Evidence timestamp: `2026-07-03T19:47:30Z`

## Clean rebuild comparison

| Invariant | Matched | Rebuild value |
| --- | --- | --- |
| Universe membership hash | `true` | `0a87fd92eadd82c37dead1d0d889fd54ce1d9fa4e65c7d9540f520649c8baa12` |
| Security count by month | `true` | `see JSON evidence` |
| Prediction count | `true` | `41024` |
| Outcome count | `true` | `40772` |
| Dataset audit decision | `true` | `review` |
| Backtest metrics | `true` | `see JSON evidence` |
| Canonical report hash | `true` | `cb98d3b0ec01df61375b10f05ad0759d5bc0d8f0afb83624e8274b8fd5dfb013` |
| Canonical audit hash | `true` | `b83d168c1d538b966cbe4c8fde0f12365d0e7e612f3cd475ad14de6f72a39845` |

## Canonical evidence

- Audit SHA-256: `b83d168c1d538b966cbe4c8fde0f12365d0e7e612f3cd475ad14de6f72a39845`
- Backtest report SHA-256: `cb98d3b0ec01df61375b10f05ad0759d5bc0d8f0afb83624e8274b8fd5dfb013`
- Backtest lineage SHA-256: `a661a8fb30cf2f88459f0ce9d9b79d81be66f5be2ced4ea0fb7c314b41648288`

## Definition of done

> For any historical monthly date, the system reconstructs the securities that were eligible then, uses only data available then, retains companies that later disappeared, and produces a deterministic baseline evaluation.
