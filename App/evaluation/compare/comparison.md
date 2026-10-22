| System | Client (4) | Held-out test (20) | Team gold (30) | All (54) |
|---|---|---|---|---|
| decidr_data_credibility (keyword judge) | 0/4 (0%) · bin 0% | 7/20 (35%) · bin 60% | 7/30 (23%) · bin 50% | 14/54 (26%) · bin 50% |
| credibility_engine_v2 (NLI) | 2/4 (50%) · bin 50% | 10/20 (50%) · bin 65% | 15/30 (50%) · bin 67% | 27/54 (50%) · bin 65% |
| org-knowledge-credibility (rules+NLI) | 4/4 (100%) · bin 100% | 10/20 (50%) · bin 80% | 15/30 (50%) · bin 70% | 29/54 (54%) · bin 76% |

| Claim | Set | Expected | decidr_data_credibility (keyword judge) | credibility_engine_v2 (NLI) | org-knowledge-credibility (rules+NLI) |
|---|---|---|---|---|---|
| E01 | client | Formal truth | ✗ Conflicted | ✗ Unsupported | ✓ SUPPORTED |
| E04 | test | SUPPORTED | ✗ Unsupported | ✗ Not enough evidence | ✓ SUPPORTED |
| E05 | test | CONTRADICTED | ✗ Conflicted | ✓ Unlikely | ✗ SUPPORTED |
| E06 | test | SUPPORTED | ✗ Conflicted | ✗ Unsupported | ✗ CONTESTED |
| E07 | test | CONTRADICTED | ✗ Conflicted | ✓ Unsupported | ✗ CONTESTED |
| E08 | test | CONTRADICTED | ✓ Unsupported | ✗ Not enough evidence | ✓ CONTRADICTED |
| E09 | client | Formal truth | ✗ Conflicted | ✓ Likely | ✓ SUPPORTED |
| E18 | test | SUPPORTED | ✗ Conflicted | ✓ Likely | ✓ SUPPORTED |
| E19 | test | SUPPORTED | ✗ Conflicted | ✗ Not enough evidence | ✓ SUPPORTED |
| E22 | client | Formal truth | ✗ Unsupported | ✗ Conflicted | ✓ SUPPORTED |
| E26 | test | CONTRADICTED | ✓ Unsupported | ✗ Not enough evidence | ✗ INSUFFICIENT_EVIDENCE |
| E27 | client | Formal truth | ✗ Conflicted | ✓ Supported | ✓ SUPPORTED |
| E28 | test | SUPPORTED | ✗ Conflicted | ✗ Conflicted | ✓ SUPPORTED |
| E31 | test | CONTRADICTED | ✓ Unsupported | ✓ Unlikely | ✗ CONTESTED |
| E32 | test | SUPPORTED | ✗ Unsupported | ✓ Likely | ✓ SUPPORTED |
| E34 | test | SUPPORTED | ✓ Supported | ✗ Not enough evidence | ✓ SUPPORTED |
| E39 | test | CONTRADICTED | ✓ Unsupported | ✓ Unsupported | ✓ CONTRADICTED |
| E41 | test | CONTRADICTED | ✗ Supported | ✓ Unlikely | ✓ CONTRADICTED |
| E42 | test | SUPPORTED | ✗ Unsupported | ✗ Unsupported | ✗ CONTESTED |
| E45 | test | INSUFFICIENT_EVIDENCE | ✗ Conflicted | ✓ Not enough evidence | ✗ CONTESTED |
| E46 | test | CONTRADICTED | ✗ Conflicted | ✓ Unlikely | ✓ CONTRADICTED |
| E47 | test | CONTRADICTED | ✓ Unsupported | ✓ Unlikely | ✗ CONTESTED |
| E49 | test | CONTRADICTED | ✗ Conflicted | ✗ Not enough evidence | ✗ CONTESTED |
| E50 | test | SUPPORTED | ✓ Supported | ✗ Not enough evidence | ✗ CONTRADICTED |
| P1-F1 | team | Supported | ✗ Conflicted | ✗ Conflicted | ✓ SUPPORTED |
| P1-P1 | team | Conflicted | ✓ Conflicted | ✓ Conflicted | ✓ CONTESTED |
| P1-S1 | team | Unsupported | ✗ Conflicted | ✓ Unlikely | ✗ CONTESTED |
| P1-P2 | team | Conflicted | ✗ Unsupported | ✗ Not enough evidence | ✗ SUPPORTED |
| P1-P3 | team | Unlikely | ✗ Conflicted | ✗ Conflicted | ✗ CONTESTED |
| P2-F1 | team | Supported | ✗ Unsupported | ✓ Likely | ✓ SUPPORTED |
| P2-G1 | team | Unsupported | ✗ Conflicted | ✗ Not enough evidence | ✗ CONTESTED |
| P2-P1 | team | Conflicted | ✓ Conflicted | ✗ Not enough evidence | ✗ SUPPORTED |
| P3-F1 | team | Unlikely | ✗ Conflicted | ✗ Supported | ✗ SUPPORTED |
| P3-P1 | team | Supported | ✗ Unsupported | ✓ Likely | ✗ CONTESTED |
| P3-G1 | team | Unsupported | ✓ Unsupported | ✓ Unlikely | ✓ CONTRADICTED |
| P4-F1 | team | Supported | ✗ Conflicted | ✓ Supported | ✓ SUPPORTED |
| P4-P1 | team | Likely | ✗ Unsupported | ✗ Not enough evidence | ✗ CONTESTED |
| P4-P2 | team | Likely | ✗ Conflicted | ✗ Not enough evidence | ✓ SUPPORTED |
| P5-F1 | team | Supported | ✗ Unsupported | ✗ Conflicted | ✓ SUPPORTED |
| P5-D1 | team | Unsupported | ✗ Conflicted | ✓ Unsupported | ✗ CONTESTED |
| P5-P1 | team | Likely | ✗ Unsupported | ✗ Unsupported | ✗ CONTESTED |
| P5-P2 | team | Conflicted | ✗ Unsupported | ✓ Conflicted | ✓ CONTESTED |
| P6-F1 | team | Supported | ✗ Conflicted | ✓ Supported | ✓ SUPPORTED |
| P7-P1 | team | Unlikely | ✓ Unsupported | ✓ Unsupported | ✓ CONTRADICTED |
| P7-P2 | team | Supported | ✗ Unsupported | ✓ Likely | ✓ SUPPORTED |
| P7-P3 | team | Unlikely | ✗ Conflicted | ✓ Unlikely | ✗ CONTESTED |
| P8-F1 | team | Supported | ✗ Unsupported | ✓ Likely | ✓ SUPPORTED |
| P8-P1 | team | Unsupported | ✓ Unsupported | ✓ Unsupported | ✗ CONTESTED |
| P8-G1 | team | Unsupported | ✓ Unsupported | ✓ Unsupported | ✓ CONTRADICTED |
| P4-F2 | team | Supported | ✗ Conflicted | ✗ Not enough evidence | ✓ SUPPORTED |
| P4-P3 | team | Conflicted | ✓ Conflicted | ✗ Not enough evidence | ✗ SUPPORTED |
| P1-F2 | team | Supported | ✗ Unsupported | ✗ Unsupported | ✗ CONTESTED |
| P5-F2 | team | Supported | ✗ Conflicted | ✗ Unsupported | ✗ CONTRADICTED |
| P3-F2 | team | Supported | ✗ Unsupported | ✗ Not enough evidence | ✓ SUPPORTED |
