#!/bin/bash
# Joggler USB drive benchmark
# Run on the Joggler to compare drives.
# Results saved to ~/bench-results.txt (appended, so run on both drives and compare).

TESTFILE=/tmp/bench_test_file
SIZE_MB=128  # 128 MB — fits in RAM for cache control, big enough to be representative
BLOCKSIZE=4M
SMALL_BS=4k
RESULTS=~/bench-results.txt

# Identify the drive
DRIVE_INFO=$(lsblk -d /dev/sda -o NAME,SIZE,MODEL,SERIAL,VENDOR --noheadings 2>/dev/null | tr -s ' ')
DATE=$(date '+%Y-%m-%d %H:%M:%S')

echo "========================================"    | tee -a "$RESULTS"
echo "Drive benchmark: $DATE"                      | tee -a "$RESULTS"
echo "Drive: $DRIVE_INFO"                          | tee -a "$RESULTS"
echo "========================================"    | tee -a "$RESULTS"

drop_cache() {
    sync
    echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null
}

# --- Sequential write (large block) ---
echo ""                                            | tee -a "$RESULTS"
echo "1. Sequential write ${SIZE_MB}MB (bs=$BLOCKSIZE):" | tee -a "$RESULTS"
rm -f "$TESTFILE"
drop_cache
RESULT=$(dd if=/dev/zero of="$TESTFILE" bs=$BLOCKSIZE count=$(( SIZE_MB / 4 )) conv=fsync 2>&1 | tail -1)
echo "   $RESULT"                                  | tee -a "$RESULTS"

# --- Sequential read (large block, from file) ---
echo ""                                            | tee -a "$RESULTS"
echo "2. Sequential read ${SIZE_MB}MB (bs=$BLOCKSIZE, from file):" | tee -a "$RESULTS"
drop_cache
RESULT=$(dd if="$TESTFILE" of=/dev/null bs=$BLOCKSIZE 2>&1 | tail -1)
echo "   $RESULT"                                  | tee -a "$RESULTS"

# --- Sequential read (raw device) ---
echo ""                                            | tee -a "$RESULTS"
echo "3. Sequential read ${SIZE_MB}MB (bs=$BLOCKSIZE, raw device /dev/sda):" | tee -a "$RESULTS"
drop_cache
RESULT=$(sudo dd if=/dev/sda of=/dev/null bs=$BLOCKSIZE count=$(( SIZE_MB / 4 )) 2>&1 | tail -1)
echo "   $RESULT"                                  | tee -a "$RESULTS"

# --- Small block write (4K) ---
echo ""                                            | tee -a "$RESULTS"
echo "4. Small block write ${SIZE_MB}MB (bs=$SMALL_BS, simulates random-ish I/O):" | tee -a "$RESULTS"
rm -f "$TESTFILE"
drop_cache
RESULT=$(dd if=/dev/zero of="$TESTFILE" bs=$SMALL_BS count=$(( SIZE_MB * 256 )) conv=fsync 2>&1 | tail -1)
echo "   $RESULT"                                  | tee -a "$RESULTS"

# --- Small block read (4K) ---
echo ""                                            | tee -a "$RESULTS"
echo "5. Small block read ${SIZE_MB}MB (bs=$SMALL_BS):" | tee -a "$RESULTS"
drop_cache
RESULT=$(dd if="$TESTFILE" of=/dev/null bs=$SMALL_BS 2>&1 | tail -1)
echo "   $RESULT"                                  | tee -a "$RESULTS"

# --- hdparm cached read (RAM speed, for reference) ---
echo ""                                            | tee -a "$RESULTS"
echo "6. hdparm cached read (RAM speed, for reference):" | tee -a "$RESULTS"
RESULT=$(sudo hdparm -T /dev/sda 2>&1 | grep "Timing")
echo "   $RESULT"                                  | tee -a "$RESULTS"

# --- hdparm direct device read (true hardware throughput) ---
echo ""                                            | tee -a "$RESULTS"
echo "7. hdparm direct device read (true hardware speed):" | tee -a "$RESULTS"
RESULT=$(sudo hdparm -t /dev/sda 2>&1 | grep "Timing")
echo "   $RESULT"                                  | tee -a "$RESULTS"

# Cleanup
rm -f "$TESTFILE"

echo ""                                            | tee -a "$RESULTS"
echo "Done. Results appended to $RESULTS"
