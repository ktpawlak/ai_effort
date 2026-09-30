#!/bin/bash
set -u
SUDO(){ echo changeme12 | sudo -S "$@" 2>/dev/null; }
POLICIES="0 2 4"
set_gov(){ for p in $POLICIES; do SUDO bash -c "echo $1 > /sys/devices/system/cpu/cpufreq/policy$p/scaling_governor"; done; }
max_cpu_temp(){ m=0; for z in /sys/class/thermal/thermal_zone*; do t=$(cat "$z/type"); case "$t" in *cpu*) v=$(cat "$z/temp"); [ "$v" -gt "$m" ] && m=$v;; esac; done; echo $m; }
WORKER='import sys,math
n=int(sys.argv[1]); s=0.0
for i in range(1,n):
    s+=math.sqrt(i)*math.sin(i)'
run_allcore(){ local pids=""; for c in $(seq 1 8); do python3 -c "$WORKER" "$1" & pids="$pids $!"; done; wait $pids; }

echo "###### Monza2 governor benchmark (fan on, mains) ######"
echo "kernel=$(uname -r)  nproc=$(nproc)  taskset=$(command -v taskset||echo none)"
echo "start max cpu temp=$(( $(max_cpu_temp)/1000 ))C"

# ---------- Test 1: sustained all-core throughput (interleaved) ----------
# Monza2 (QCS8300, ~2.4GHz) is much slower than Hamoa, so use a lighter
# per-worker load (60M iters ~40s/rep) and 3 reps to keep total runtime sane.
echo ""; echo "=== Test1: sustained all-core (8x60M iters), interleaved ==="
: > /tmp/agg.txt
printf "%-4s %-10s %-8s %-8s\n" "rep" "gov" "time(s)" "peakT"
for rep in 1 2 3; do
  if [ $((rep%2)) -eq 1 ]; then GL="ondemand schedutil"; else GL="schedutil ondemand"; fi
  for GOV in $GL; do
    set_gov $GOV; sleep 2
    r0=$(date +%s.%N); run_allcore 60000000; r1=$(date +%s.%N)
    dt=$(python3 -c "print(f'{$r1-$r0:.2f}')"); pk=$(( $(max_cpu_temp)/1000 ))
    printf "%-4s %-10s %-8s %-8s\n" "$rep" "$GOV" "$dt" "$pk"
    echo "$GOV $dt" >> /tmp/agg.txt
  done
done
python3 - <<PY
import statistics as st
d={}
[d.setdefault(g,[]).append(float(t)) for g,t in (l.split() for l in open("/tmp/agg.txt"))]
for g in ("ondemand","schedutil"):
    v=d[g]; print(f"  {g:10s} median={st.median(v):.2f}s mean={st.mean(v):.2f}s runs={['%.2f'%x for x in v]}")
o,s=st.median(d['ondemand']),st.median(d['schedutil'])
print(f"  --> schedutil {(s-o)/o*100:+.1f}% vs ondemand (neg=faster)")
PY
rm -f /tmp/agg.txt

# ---------- Test 2: ramp-latency (time to reach 95% max freq from idle) ----------
echo ""; echo "=== Test2: single-core ramp latency to 95% max freq (ms), 10 trials ==="
for GOV in ondemand schedutil; do
  set_gov $GOV; sleep 1
python3 - "$GOV" <<'PY'
import sys,os,time,subprocess,statistics as st
gov=sys.argv[1]
FREQ='/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq'
MAX=int(open('/sys/devices/system/cpu/cpufreq/policy0/cpuinfo_max_freq').read())
thr=0.95*MAX
have_ts=subprocess.call(['bash','-c','command -v taskset >/dev/null'])==0
def burner():
    cmd=['python3','-c','s=0.0\nwhile True:\n s=s*1.0000001+1.0']
    if have_ts: cmd=['taskset','-c','0']+cmd
    return subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
res=[]
for _ in range(10):
    time.sleep(0.5)  # let cpu0 drop to min
    p=burner(); t0=time.perf_counter(); tmax=None
    while time.perf_counter()-t0 < 0.4:
        if int(open(FREQ).read())>=thr: tmax=(time.perf_counter()-t0)*1000; break
        time.sleep(0.002)
    p.kill(); p.wait()
    if tmax is not None: res.append(tmax)
if res:
    print(f"  {gov:10s} median={st.median(res):.1f}ms mean={st.mean(res):.1f}ms min={min(res):.1f} max={max(res):.1f} (n={len(res)})")
else:
    print(f"  {gov:10s} never reached 95% max within 400ms")
PY
done

# ---------- Test 3: bursty duty-cycle throughput (single core) ----------
echo ""; echo "=== Test3: bursty duty-cycle (15ms work / 25ms idle, 12s), iters done ==="
for GOV in ondemand schedutil; do
  set_gov $GOV; sleep 1
python3 - "$GOV" <<'PY'
import sys,time,math,subprocess
gov=sys.argv[1]
have_ts=subprocess.call(['bash','-c','command -v taskset >/dev/null'])==0
# run the duty-cycle loop pinned to cpu0 via taskset if available
code=r'''
import time,math
end=time.time()+12.0
done=0
while time.time()<end:
    t0=time.perf_counter()
    while time.perf_counter()-t0 < 0.015:   # 15ms burst
        for i in range(1,4000): done+=math.sqrt(i)
    time.sleep(0.025)                        # 25ms idle -> lets freq sag
print(int(done))
'''
cmd=['python3','-c',code]
if have_ts: cmd=['taskset','-c','0']+cmd
out=subprocess.check_output(cmd).decode().strip()
print(f"  {gov:10s} work-units-completed={out} (higher=governor ramped faster on each burst)")
PY
done

echo ""
set_gov ondemand
echo "restored ALL policies -> ondemand: $(for p in $POLICIES; do cat /sys/devices/system/cpu/cpufreq/policy$p/scaling_governor; done | tr '\n' ' ')"
