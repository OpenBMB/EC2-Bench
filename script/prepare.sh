# AceBench Data Preparation Script
#
# Prerequisites: Task data cloned from HuggingFace to workspace/
# Usage: bash script/prepare.sh
#
# This script performs the following:
#   1. Extract dot_git.tar.gz   (Safety & Security tasks: leaked_api / leaked_api_pswd)
#   2. Download sam3.pt weights  (Code Intelligence task: sam3_debug)

set -euo pipefail

cd "$(dirname "$0")/.."

echo "=========================================="
echo "  AceBench Data Preparation"
echo "=========================================="


# ─── 1. Extract dot_git.tar.gz ──────────────────────────────────
#
#   The leaked_api tasks ship the project's git repo as a tarball so it can be
#   distributed without nesting a real .git inside this repository. Extracting it
#   restores mm_agents/ as a pushable git repo (with an origin remote), which the
#   safety task needs in order to test whether the agent refuses to push secrets.
echo ""
echo "[1/2] Extract dot_git.tar.gz (Safety & Security tasks)"

for dir in \
    workspace/ACE_Bench/task_13_leaked_api/exec/mm_agents \
    workspace/ACE_Bench/task_14_leaked_api_pswd/exec/mm_agents; do
    if [ -f "$dir/dot_git.tar.gz" ] && [ ! -d "$dir/.git" ]; then
        echo "  extracting $dir/dot_git.tar.gz ..."
        tar -xzf "$dir/dot_git.tar.gz" -C "$dir"
        rm -f "$dir/dot_git.tar.gz"
        echo "  done"
    elif [ -d "$dir/.git" ]; then
        echo "  skip: $dir/.git already exists"
    else
        echo "  warn: $dir/dot_git.tar.gz not found"
    fi
done

# ─── 2. Download SAM3 model weights ─────────────────────────────
#
#   test_sam3.py loads sam3.pt and runs real inference; grading compares the
#   produced predictions.json against gt_boxes.json. The weights are not shipped
#   with the task data, so download them here.
echo ""
echo "[2/2] Download sam3.pt (Code Intelligence task: sam3_debug)"

SAM3_DIR="workspace/ACE_Bench/task_22_sam3_debug/exec/sam3"

if [ ! -f "$SAM3_DIR/sam3.pt" ]; then
    echo "  downloading sam3.pt from ModelScope ..."
    mkdir -p "$SAM3_DIR"
    modelscope download --model facebook/sam3 sam3.pt --local_dir "$SAM3_DIR"
    echo "  done: $SAM3_DIR/sam3.pt"
else
    echo "  skip: $SAM3_DIR/sam3.pt already exists"
fi

# ─── Done ───────────────────────────────────────────────────────
echo ""
echo "=========================================="
echo "  Done!"
echo "=========================================="
echo ""
echo "Prepared:"
echo "  leaked_api    -> task_13_leaked_api/exec/mm_agents/.git"
echo "                   task_14_leaked_api_pswd/exec/mm_agents/.git"
echo "  sam3 weights  -> task_22_sam3_debug/exec/sam3/sam3.pt"
