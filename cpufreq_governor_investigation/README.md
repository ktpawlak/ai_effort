# cpufreq governor investigation — `ondemand` vs `schedutil` on Qualcomm ARM

**Question that started this:** *Why were AMD (x86) kernels switched to the
`schedutil` CPU governor while ARM stayed on `ondemand`?* — and, following from
that, **can we safely switch our Qualcomm ARM boards to `schedutil`?**

**Short answer:** It depends entirely on whether the board's cpufreq **driver**
offers a working `fast_switch` path. Hamoa (X1E80100, SCMI) does → `schedutil`
is a clean, free swap. Monza2 (QCS8300, `qcom-cpufreq-hw` with interconnect
scaling) does **not** → `schedutil` still works but falls back to a deferred
`sugov` kthread and measurably regresses ramp/interactive performance. This is
exactly the case Ubuntu's conservative arm64 `ondemand` default guards against.

---

## 1. The Ubuntu config that prompted the question

Identical in both kernel trees
(`debian.master/config/annotations`, Noble `6.8` and Resolute `7.0`):

| Arch | Default governor (`CONFIG_CPU_FREQ_DEFAULT_GOV_*`) |
|------|----------------------------------------------------|
| amd64 | `SCHEDUTIL` |
| arm64 / armhf / ppc64el | `ONDEMAND` |
| riscv64 | `PERFORMANCE` (note: "for bootspeed") |

There is **no `note<>`** in the annotations explaining the arm64 choice; it is
inherited from the initial Ubuntu import, i.e. a conservative arch-wide default,
not a Qualcomm-specific decision. `schedutil` is still *compiled in*
(`CONFIG_CPU_FREQ_GOV_SCHEDUTIL=y`) and available at runtime on every arch.

---

## 2. Background — two orthogonal layers

```
GOVERNOR  (policy: "what freq SHOULD we run?")   ondemand, schedutil, performance…
   │  calls down via the cpufreq core
DRIVER    (mechanism: "how do we ACTUALLY change it?")   scmi-cpufreq, qcom-cpufreq-hw…
   │
hardware / firmware
```

Governors and drivers are independent — any governor runs on any driver.

### The drivers (the "how")
- **`scmi-cpufreq`** (Hamoa/X1E80100): does not touch clocks directly; asks the
  **SCMI firmware** to set a performance level, over a mailbox **or a fast
  channel** (shared-memory doorbell). Firmware-abstracted.
- **`qcom-cpufreq-hw`** (Monza2/QCS8300): talks straight to the Qualcomm
  EPSS/OSM block; a frequency change is a **register write** of a precomputed
  LUT index. Direct hardware.

### The governors (the "what")
- **`ondemand`**: timer-sampled. Periodically reads CPU busy% and ramps toward
  max on high load. Driver-agnostic, only ever needs the (sleep-allowed)
  `.target_index()` driver hook.
- **`schedutil`**: scheduler-integrated. Picks frequency from the scheduler's
  PELT utilization signal, ideally **inline** from scheduler context (rq locks
  held, IRQs off) — where **sleeping is forbidden**.

### The `fast_switch` contract (the crux)

A driver exposes two entry points:

| Driver hook | Constraint | Used by |
|-------------|-----------|---------|
| `.target_index()` | **may sleep** (mutexes, firmware round-trips) | ondemand; schedutil fallback |
| `.fast_switch()`  | **atomic, non-sleeping** | schedutil fast path |

`schedutil` checks `policy->fast_switch_possible`:
- **true**  → calls `.fast_switch()` inline. Minimal latency.
- **false** → defers to a per-policy **`sugov:N` kthread**, which calls the
  sleeping `.target_index()`. Correct, but adds wakeup latency + jitter.

**`ondemand` never needs the fast path** → it works well on *any* driver. That
is why it is the safe default.

**Diagnostic:** if a policy is on `schedutil` and a **`sugov:N` kthread exists**,
fast_switch is OFF for that policy (slow deferred path). No `sugov` thread while
schedutil is active → fast path in use.

---

## 3. Findings per board

### Hamoa — X1E80100, Resolute, `7.0.0-1013-qcom`

- `scaling_driver = scmi`.
- SCMI transport is `scmi_mailbox_transport`, **but** dmesg shows
  `Enabling SCMI Quirk [quirk_perf_level_get_fc_force]` — the driver forces use
  of the SCMI **perf fast channel** (shared-memory, atomic-safe).
- `scmi-cpufreq.c` sets
  `policy->fast_switch_possible = perf_ops->fast_switch_possible(ph, domain)` —
  i.e. it trusts firmware; firmware (`Qualcomm: 0x20000`) advertises fast
  channels.
- **Runtime proof:** switching a policy to `schedutil` produced **no `sugov`
  kthread** → fast path active.
- Energy Model present (3 perf domains created: cpu0/4/8).

**Verdict: Hamoa cleanly supports `schedutil` (atomic fast path).**

### Monza2 — QCS8300, Noble, `6.8.0-1082-qcom`

- `scaling_driver = qcom-cpufreq-hw` (all 3 policies: cpu0-1, cpu2-3, cpu4-7).
- **Runtime proof:** each policy switched to `schedutil` spawns a `sugov:N`
  kthread (`sugov:0`, `sugov:2`, `sugov:4` all observed) → fast_switch is
  **OFF** for every cluster.
- **Root cause — the interconnect gotcha (confirmed in source + DT):**
  - `cpu@0` DT node has both `operating-points-v2` **and** `interconnects`.
  - In `drivers/cpufreq/qcom-cpufreq-hw.c`, `qcom_cpufreq_hw_read_lut()`:
    `dev_pm_opp_of_add_table()` succeeds (ret == 0) → `icc_scaling_enabled =
    true`. The line `policy->fast_switch_possible = true;` lives **only** in the
    `else` (`ret == -ENODEV`, no-OPP) branch and is never reached.
  - Because CPU frequency is tied to **interconnect (DDR/L3 bandwidth) votes**
    through the ICC framework — which can sleep — the driver deliberately
    forbids the atomic fast path.

**Verdict: Monza2 does NOT cleanly support `schedutil` — it works only via the
deferred `sugov` kthread (slow path).**

> Note observed on Monza2: policies 0 and 4 were found already on `schedutil`
> and policy2 on `ondemand` (a pre-existing mixed state, no service setting it —
> likely leftover manual experimentation). All three were restored to
> `ondemand` at the end of testing.

---

## 4. Benchmark results

Workload: dependency-free `python3` math kernel (`sqrt*sin` loop); no packages
installed on the boards. Governors interleaved per rep to cancel ordering/heat
bias. Both boards had an **external fan** attached during sustained tests, and
Monza2 stayed at 33–41 °C (no thermal throttling). Scripts: see `bench/`.

### Hamoa (fan on, mains) — `bench/hamoa_gov_bench.sh`

| Test | ondemand | schedutil | Verdict |
|------|----------|-----------|---------|
| Sustained all-core (12×200M, median) | 31.12 s | 31.12 s | identical (+0.0%) |
| Single-core burst (12M, median) | 1.445 s | 1.450 s | equal (noise) |

→ On a board **with** fast_switch, `schedutil` matches `ondemand`.

### Monza2 (fan on, mains) — `bench/monza_gov_bench.sh`

| Test | ondemand | schedutil | Verdict |
|------|----------|-----------|---------|
| Sustained all-core (8×200M, median) | 125.9 s | 122.7 s | ≈ equal (−2.5%, noise) |
| **Ramp latency** to 95% max freq (median) | **11.3 ms** | **23.1 ms** (max 71 ms) | schedutil ~2× slower, jittery |
| **Bursty** duty-cycle work done (15ms on / 25ms off) | **801 M** | **476 M** | ondemand did **+68% more work** |

→ On a board **without** fast_switch, `schedutil` is fine for pinned sustained
load but clearly regresses **ramp latency and bursty/interactive** performance —
the deferred `sugov` kthread wakeup is the cause.

**Why sustained load hides it:** once all cores are pinned at max, no further
frequency changes happen, so the deferred path never fires. The penalty only
shows on **transitions** (ramp, bursty, interactive), which is precisely what a
compute-throughput test misses and what real desktop/interactive use hits.

---

## 5. Conclusion

- Ubuntu's arm64 `ondemand` default is a **safe, driver-agnostic** choice, not a
  bug or oversight. It works regardless of fast_switch support.
- `schedutil` is only as good as the **driver's `fast_switch`** support:
  - **Hamoa (SCMI fast channel):** clean — could switch with no regression.
  - **Monza2 (`qcom-cpufreq-hw` + icc scaling):** works but regresses
    ramp/interactive load → do **not** blanket-switch.
- The proper fix for Monza2 is not "avoid schedutil" but **make the driver offer
  fast_switch** — i.e. decouple CPU frequency changes from the sleeping
  interconnect vote (async/deferred ICC scaling), so
  `policy->fast_switch_possible` can be true even with icc scaling. That is an
  upstream `qcom-cpufreq-hw` change, not a config tweak.

---

## 6. How to reproduce

```bash
# quick per-board fast_switch check (schedutil on => look for sugov kthreads)
ssh ubuntu@<board> '
  cat /sys/devices/system/cpu/cpufreq/policy*/scaling_driver | sort -u
  cat /sys/devices/system/cpu/cpufreq/policy*/scaling_governor
  ps -eo comm | grep "^sugov:" || echo "no sugov (fast path or non-schedutil)"'

# full governor benchmark (interleaved; restores ondemand at end)
scp bench/monza_gov_bench.sh ubuntu@192.168.1.185:/tmp/   # or hamoa_gov_bench.sh -> .123
ssh ubuntu@<board> 'bash -s' < bench/<script>
```

Boards use SSH password `changeme12` (see repo instructions; the scripts embed
`sudo` via `echo changeme12 | sudo -S`). The scripts **restore all policies to
`ondemand`** when finished.

Source references:
- `~/qualcomm/linux/drivers/cpufreq/scmi-cpufreq.c` (Hamoa fast_switch logic)
- `~/canonical/kernel/ubuntu/noble/linux/drivers/cpufreq/qcom-cpufreq-hw.c`
  (`fast_switch_possible` / `icc_scaling_enabled`, ~lines 217–235)
- `debian.master/config/annotations` (`CPU_FREQ_DEFAULT_GOV_*`) in both trees
