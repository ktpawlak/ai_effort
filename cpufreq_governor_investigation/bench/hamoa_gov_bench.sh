#!/bin/bash
set -u
SUDO(){ echo changeme12 | sudo -S "$@" 2>/dev/null; }
POLICIES="0 4 8"
set_gov(){ for p in $POLICIES; do SUDO bash -c "echo $1 > /sys/devices/system/cpu/cpufreq/policy$p/scaling_governor"; done; }
max_cpu_temp(){ m=0; for z in /sys/class/thermal/thermal_zone*; do t=$(cat "$z/type"); case "$t" in *cpu*) v=$(cat "$z/temp"); [ "$v" -gt "$m" ] && m=$v;; esac; done; echo $m; }
WORKER='import sys,math
n=int(sys.argv[1]); s=0.0
for i in range(1,n):
    s+=math.sqrt(i)*math.sin(i)'
run_allcore(){ local pids=""; for c in $(seq 1 12); do python3 -c "$WORKER" "$1" & pids="$pids $!"; done; wait $pids; }

peak_run(){ # $1 iters ; echoes: time peaktemp avgf0 avgf4 avgf8 minf
  local iters=$1 sf=/tmp/pk.txt; : > $sf
  ( while :; do echo "$(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq) $(cat /sys/devices/system/cpu/cpufreq/policy4/scaling_cur_freq) $(cat /sys/devices/system/cpu/cpufreq/policy8/scaling_cur_freq) $(max_cpu_temp)" >> $sf; sleep 0.3; done ) & local sp=$!
  local r0=$(date +%s.%N); run_allcore $iters; local r1=$(date +%s.%N)
  kill $sp 2>/dev/null; wait $sp 2>/dev/null
  python3 - "$sf" "$r0" "$r1" <<PY
import sys,statistics as st
rows=[l.split() for l in open(sys.argv[1]) if l.strip()]
dt=float(sys.argv[3])-float(sys.argv[2])
f0=[int(r[0]) for r in rows]; f4=[int(r[1]) for r in rows]; f8=[int(r[2]) for r in rows]; tp=[int(r[3]) for r in rows]
print(f"{dt:.2f} {max(tp)/1000:.1f} {int(st.mean(f0)//1000)} {int(st.mean(f4)//1000)} {int(st.mean(f8)//1000)} {int(min(min(f0),min(f4),min(f8))//1000)}")
PY
PY
}

echo "###### Hamoa governor benchmark v3 (interleaved, active cooling, mains) ######"
echo "kernel=$(uname -r)  start max cpu temp=$(( $(max_cpu_temp)/1000 ))C"
ITERS=200000000  # ~24s all-core sustained
: > /tmp/agg.txt
echo ""
printf "%-4s %-10s %-8s %-8s %-8s %-8s %-8s %-8s\n" "rep" "gov" "time(s)" "peakT" "f0MHz" "f4MHz" "f8MHz" "minMHz"
for rep in 1 2 3 4; do
  if [ $((rep % 2)) -eq 1 ]; then GLIST="ondemand schedutil"; else GLIST="schedutil ondemand"; fi
  for GOV in $GLIST; do
    set_gov $GOV; sleep 2
    res=$(peak_run $ITERS); set -- $res
    printf "%-4s %-10s %-8s %-8s %-8s %-8s %-8s %-8s\n" "$rep" "$GOV" "$1" "$2" "$3" "$4" "$5" "$6"
    echo "$GOV $1" >> /tmp/agg.txt
  done
done
echo ""
echo "=== sustained all-core throughput (lower time = better) ==="
python3 - <<PY
import statistics as st
d={}
for l in open("/tmp/agg.txt"):
    g,t=l.split(); d.setdefault(g,[]).append(float(t))
for g in ("ondemand","schedutil"):
    v=d.get(g,[]); 
    if v: print(f"  {g:10s} median={st.median(v):.2f}s mean={st.mean(v):.2f}s runs={['%.2f'%x for x in v]}")
o=st.median(d.get('ondemand',[0])); s=st.median(d.get('schedutil',[0]))
if o and s: print(f"  --> schedutil is {(s-o)/o*100:+.1f}% vs ondemand (neg=faster)")
PY

echo ""
echo "=== single-core ramp/latency: short bursts from idle ==="
for GOV in ondemand schedutil; do
  set_gov $GOV; sleep 1; bt=""
  for rep in $(seq 1 10); do
    sleep 0.8
    b0=$(date +%s.%N); python3 -c "$WORKER" 12000000 >/dev/null; b1=$(date +%s.%N)
    bt="$bt $(python3 -c "print(f'{$b1-$b0:.3f}')")"
  done
  med=$(python3 -c "import statistics as st; print(f'{st.median([$(echo $bt|tr ' ' ',')]):.3f}')")
  printf "  %-10s median=%ss  runs:%s\n" "$GOV" "$med" "$bt"
done
rm -f /tmp/agg.txt
set_gov ondemand
echo ""
echo "restored governor -> $(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor)"
