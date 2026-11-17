# Experiment 4: online combination with "balance stays"

Made with `python -m eval.combination run`. Rule: 2026-10-17 experiment 4 (e510b1b), readings of 2026-11-02. Nothing here changes a default and nothing is adopted. **Variants counted: 3** ((i) fixed 50/50, (ii) one global weight, (iii) per-account weights); scaling the futures and replacing a share of them were not tried. Condition 12 (my account can only veto) is not judged here. The synthetic sets are never pooled.

## 12-month set

Origins: 800, of which 726 not already below the line; accounts: 20.

**Origins that get a learned weight** (counted from the dates before scoring): (ii) one global weight 680 of 800 (85.0%), 20 accounts; (iii) per-account 560 of 800 (70.0%), 20 accounts.

**Learned weights by archetype** (mean w over origins; share of origins with w < 1):

| archetype | (ii) | (iii) |
|---|---|---|
| DHOME | 0.99 (15%) | 0.80 (60%) |
| DRENT | 0.99 (15%) | 0.84 (42%) |
| FREE | 0.99 (15%) | 0.52 (70%) |
| IFAM | 0.99 (12%) | 0.89 (47%) |
| ITIGHT | 0.99 (15%) | 0.62 (70%) |
| SAL | 0.99 (15%) | 0.97 (31%) |
| SEM | 0.99 (15%) | 0.97 (15%) |
| TIGHT | 0.97 (18%) | 0.95 (45%) |

### (i) fixed 50/50: does not pass

- **1 unchanged where it should be: pass.** origins at w = 1: 0, differing from the default: 0; saved futures equal to the experiment 3 records: 800 of 800.
- **2 no harm: FAIL.** archetypes worse by more than 1%: DHOME, DRENT, IFAM, ITIGHT, SAL, SEM, TIGHT (of 8); personas worse by more than 5%: v2-DHOME-01, v2-DHOME-02, v2-DHOME-03, v2-DRENT-01, v2-DRENT-02, v2-DRENT-03, v2-IFAM-01, v2-IFAM-02, v2-IFAM-03, v2-SAL-01, v2-SAL-02, v2-SAL-03, v2-SEM-01, v2-TIGHT-01, v2-TIGHT-02, v2-TIGHT-03 (of 20).
- **3 a real gain: FAIL.** net better: FREE (1; 2 needed); mean relative change over archetypes: 30-day error +11.4%, absolute bias +252.3% (both must be below 0).
- **4 honest band: FAIL.** coverage further from 80% by more than 2 points: DHOME, DRENT, IFAM, ITIGHT, SAL, SEM, TIGHT; 30-day band wider by more than 10%: none.
- **5 CRPS not just flatter: FAIL.** mean CRPS skill against balance stays: default +0.4191, variant +0.3250.
- **6 dips caught must not fall: FAIL.** dips caught on ahead: default 47 (threshold 0.35), variant 43 (threshold 0.2); shortfall episodes 52.
- **7 dip Brier skill must not fall: FAIL.** mean Brier skill against balance stays over 3 archetypes with 5+ dips (125 dips): default +0.6298, variant +0.4252.
- **11 synthetic and Berka agree: FAIL.** mean CRPS: default $900.89, variant $990.21 (must be strictly lower).

### (ii) one global weight: does not pass

- **1 unchanged where it should be: pass.** origins at w = 1: 680, differing from the default: 0; saved futures equal to the experiment 3 records: 800 of 800.
- **2 no harm: FAIL.** archetypes worse by more than 1%: DHOME, DRENT, SAL, SEM, TIGHT (of 8); personas worse by more than 5%: v2-DRENT-01, v2-DRENT-03, v2-SAL-01, v2-SAL-02, v2-SAL-03, v2-SEM-01, v2-TIGHT-01, v2-TIGHT-02, v2-TIGHT-03 (of 20).
- **3 a real gain: FAIL.** net better: none (0; 2 needed); mean relative change over archetypes: 30-day error +0.7%, absolute bias +11.0% (both must be below 0).
- **4 honest band: FAIL.** coverage further from 80% by more than 2 points: SAL; 30-day band wider by more than 10%: none.
- **5 CRPS not just flatter: FAIL.** mean CRPS skill against balance stays: default +0.4191, variant +0.4177.
- **6 dips caught must not fall: FAIL.** dips caught on ahead: default 47 (threshold 0.35), variant 46 (threshold 0.35); shortfall episodes 52.
- **7 dip Brier skill must not fall: FAIL.** mean Brier skill against balance stays over 3 archetypes with 5+ dips (125 dips): default +0.6298, variant +0.6114.
- **11 synthetic and Berka agree: FAIL.** mean CRPS: default $900.89, variant $902.18 (must be strictly lower).

### (iii) per-account: does not pass

- **1 unchanged where it should be: pass.** origins at w = 1: 412, differing from the default: 0; saved futures equal to the experiment 3 records: 800 of 800.
- **2 no harm: FAIL.** archetypes worse by more than 1%: DHOME, DRENT, IFAM, ITIGHT, SAL, SEM, TIGHT (of 8); personas worse by more than 5%: v2-DHOME-02, v2-DHOME-03, v2-DRENT-02, v2-DRENT-03, v2-IFAM-02, v2-IFAM-03, v2-SAL-01, v2-SAL-02, v2-SAL-03, v2-SEM-01, v2-TIGHT-01, v2-TIGHT-02, v2-TIGHT-03 (of 20).
- **3 a real gain: FAIL.** net better: FREE (1; 2 needed); mean relative change over archetypes: 30-day error -0.9%, absolute bias +34.8% (both must be below 0).
- **4 honest band: FAIL.** coverage further from 80% by more than 2 points: DRENT, IFAM, ITIGHT, SAL, SEM; 30-day band wider by more than 10%: none.
- **5 CRPS not just flatter: pass.** mean CRPS skill against balance stays: default +0.4191, variant +0.4194.
- **6 dips caught must not fall: pass.** dips caught on ahead: default 47 (threshold 0.35), variant 48 (threshold 0.2); shortfall episodes 52.
- **7 dip Brier skill must not fall: FAIL.** mean Brier skill against balance stays over 3 archetypes with 5+ dips (125 dips): default +0.6298, variant +0.6093.
- **11 synthetic and Berka agree: FAIL.** mean CRPS: default $900.89, variant $902.69 (must be strictly lower).

## 24-month set

Origins: 800, of which 717 not already below the line; accounts: 20.

**Origins that get a learned weight** (counted from the dates before scoring): (ii) one global weight 680 of 800 (85.0%), 20 accounts; (iii) per-account 560 of 800 (70.0%), 20 accounts.

**Learned weights by archetype** (mean w over origins; share of origins with w < 1):

| archetype | (ii) | (iii) |
|---|---|---|
| DHOME | 1.00 (0%) | 0.82 (46%) |
| DRENT | 1.00 (0%) | 0.87 (45%) |
| FREE | 1.00 (0%) | 0.86 (70%) |
| IFAM | 1.00 (0%) | 0.95 (23%) |
| ITIGHT | 1.00 (0%) | 0.77 (69%) |
| SAL | 1.00 (0%) | 0.99 (13%) |
| SEM | 1.00 (0%) | 1.00 (0%) |
| TIGHT | 1.00 (0%) | 0.99 (11%) |

### (i) fixed 50/50: does not pass

- **1 unchanged where it should be: pass.** origins at w = 1: 0, differing from the default: 0; saved futures equal to the experiment 3 records: 800 of 800.
- **2 no harm: FAIL.** archetypes worse by more than 1%: DHOME, DRENT, FREE, IFAM, ITIGHT, SAL, SEM, TIGHT (of 8); personas worse by more than 5%: v2L-DHOME-01, v2L-DHOME-02, v2L-DHOME-03, v2L-DRENT-01, v2L-DRENT-02, v2L-DRENT-03, v2L-FREE-01, v2L-IFAM-01, v2L-IFAM-03, v2L-SAL-01, v2L-SAL-02, v2L-SAL-03, v2L-SEM-01, v2L-TIGHT-01, v2L-TIGHT-02, v2L-TIGHT-03 (of 20).
- **3 a real gain: FAIL.** net better: none (0; 2 needed); mean relative change over archetypes: 30-day error +35.5%, absolute bias +863.0% (both must be below 0).
- **4 honest band: FAIL.** coverage further from 80% by more than 2 points: DHOME, DRENT, FREE, IFAM, ITIGHT, SAL, SEM, TIGHT; 30-day band wider by more than 10%: none.
- **5 CRPS not just flatter: FAIL.** mean CRPS skill against balance stays: default +0.4810, variant +0.3534.
- **6 dips caught must not fall: FAIL.** dips caught on ahead: default 45 (threshold 0.4), variant 44 (threshold 0.15); shortfall episodes 51.
- **7 dip Brier skill must not fall: FAIL.** mean Brier skill against balance stays over 4 archetypes with 5+ dips (123 dips): default +0.6290, variant +0.4646.
- **11 synthetic and Berka agree: FAIL.** mean CRPS: default $816.32, variant $935.54 (must be strictly lower).

### (ii) one global weight: does not pass

- **1 unchanged where it should be: pass.** origins at w = 1: 800, differing from the default: 0; saved futures equal to the experiment 3 records: 800 of 800.
- **2 no harm: pass.** archetypes worse by more than 1%: none (of 8); personas worse by more than 5%: none (of 20).
- **3 a real gain: FAIL.** net better: none (0; 2 needed); mean relative change over archetypes: 30-day error +0.0%, absolute bias +0.0% (both must be below 0).
- **4 honest band: pass.** coverage further from 80% by more than 2 points: none; 30-day band wider by more than 10%: none.
- **5 CRPS not just flatter: pass.** mean CRPS skill against balance stays: default +0.4810, variant +0.4810.
- **6 dips caught must not fall: pass.** dips caught on ahead: default 45 (threshold 0.4), variant 45 (threshold 0.4); shortfall episodes 51.
- **7 dip Brier skill must not fall: pass.** mean Brier skill against balance stays over 4 archetypes with 5+ dips (123 dips): default +0.6290, variant +0.6290.
- **11 synthetic and Berka agree: FAIL.** mean CRPS: default $816.32, variant $816.32 (must be strictly lower).

### (iii) per-account: does not pass

- **1 unchanged where it should be: pass.** origins at w = 1: 523, differing from the default: 0; saved futures equal to the experiment 3 records: 800 of 800.
- **2 no harm: FAIL.** archetypes worse by more than 1%: DHOME, DRENT, ITIGHT, SAL (of 8); personas worse by more than 5%: v2L-DHOME-01, v2L-DHOME-03, v2L-DRENT-02, v2L-DRENT-03, v2L-SAL-03 (of 20).
- **3 a real gain: FAIL.** net better: FREE (1; 2 needed); mean relative change over archetypes: 30-day error -1.8%, absolute bias +19.7% (both must be below 0).
- **4 honest band: FAIL.** coverage further from 80% by more than 2 points: ITIGHT; 30-day band wider by more than 10%: none.
- **5 CRPS not just flatter: pass.** mean CRPS skill against balance stays: default +0.4810, variant +0.4820.
- **6 dips caught must not fall: pass.** dips caught on ahead: default 45 (threshold 0.4), variant 45 (threshold 0.35); shortfall episodes 51.
- **7 dip Brier skill must not fall: pass.** mean Brier skill against balance stays over 4 archetypes with 5+ dips (123 dips): default +0.6290, variant +0.6304.
- **11 synthetic and Berka agree: FAIL.** mean CRPS: default $816.32, variant $816.56 (must be strictly lower).

## Verdict by condition (judged, not decided: the decision is mine)

| condition | 12 i | 12 ii | 12 iii | long i | long ii | long iii |
|---|---|---|---|---|---|---|
| 1 unchanged where it should be | pass | pass | pass | pass | pass | pass |
| 2 no harm | FAIL | FAIL | FAIL | FAIL | pass | FAIL |
| 3 a real gain | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| 4 honest band | FAIL | FAIL | FAIL | FAIL | pass | FAIL |
| 5 CRPS not just flatter | FAIL | FAIL | pass | FAIL | pass | pass |
| 6 dips caught must not fall | FAIL | FAIL | pass | FAIL | pass | pass |
| 7 dip Brier skill must not fall | FAIL | FAIL | FAIL | FAIL | pass | pass |
| 8 Berka margin | - | - | - | - | - | - |
| 9 Berka other measures | - | - | - | - | - | - |
| 10 vetoes | - | - | - | - | - | - |
| 11 synthetic and Berka agree | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| 12 my account can only veto | not judged | not judged | not judged | not judged | not judged | not judged |
