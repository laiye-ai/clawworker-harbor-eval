# Sourced by the fixture entrypoint and Harbor's noninteractive bash execs.
# The container hostname is stable within a trial and different across trials.
export CLAWWORKER_WORKER_ID="prun_$(hostname | sha256sum | cut -c1-32)"
_ap_fixture_workspace="/workspace/native-user/${CLAWWORKER_OWNER_ID}/workspaces/${CLAWWORKER_WORKER_ID}"
if [ ! -L /workspace/work ]; then
    mkdir -p "$_ap_fixture_workspace" || exit 1
    if [ -e /workspace/work ]; then
        rmdir /workspace/work || exit 1
    fi
    ln -s "$_ap_fixture_workspace" /workspace/work || exit 1
fi
unset _ap_fixture_workspace
