# Unreal: task-scoped rules

- Manager: read the .uproject/.uplugin, owning module and pinned engine association; identify reflected C++/Blueprint or serialized/binary contracts before assigning ownership.
- Worker: keep module dependencies, build target guards, UCLASS/USTRUCT/UPROPERTY/UFUNCTION metadata and generated-header conventions consistent. A reflected rename may require redirects and Blueprint validation; do not hand-edit generated reflection output.
- Treat .uasset/.umap and OFPA external actor/object files as authored assets with exclusive ownership. Preserve source-control locks and use existing Editor tooling for asset changes. Do not broadly regenerate/resave assets or assume packaged plugin Binaries are disposable.
- Profile affected tick paths, allocation/object lifetime, async loading, draw calls and resource residency against a stated scenario. Disable unnecessary ticking only where semantics permit; follow UObject/GC ownership instead of inventing cleanup rules.
- Exclude Intermediate, Saved logs/cooks, DerivedDataCache and bulk binaries from reads. Use one module/asset query and bounded UBT/Editor diagnostics. Writable build/import caches and Editor resources belong to each workspace.
- Reviewer: check reflection/Blueprint compatibility and authored external actor changes. Run the existing narrow module/target build or targeted automation; validate touched Blueprint/asset references in the Editor where needed. Compilation does not prove asset or visual correctness.

Version-specific source: https://dev.epicgames.com/documentation/en-us/unreal-engine/one-file-per-actor-in-unreal-engine
