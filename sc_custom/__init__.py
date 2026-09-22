__version__ = "0.0.1"

from sc_custom.overrides.serial_batch_storage import apply_monkey_patches
from sc_custom.overrides.pick_list_override import apply_pick_list_patches
from sc_custom.overrides.ebics_disable_guard import apply_ebics_disable_guard
from sc_custom.overrides.ebics_btf_version_patch import apply_ebics_btf_version_patch

apply_monkey_patches()
apply_pick_list_patches()
apply_ebics_disable_guard()
apply_ebics_btf_version_patch()
