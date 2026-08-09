# Mod Identity

## Identity Levels

Launcher code must keep these identities separate:

- `ModDescriptor.mod_id` is declared inside a JAR and identifies a runtime mod.
- `modrinth_project_id` and `modrinth_version_id` are remote provenance stored by the launcher.
- normalized title, slug, declared ID, and filename are display or discovery hints.

`ModIdentityService.match_project()` returns one explicit result:

- `OWNED`: exactly one installed item has the requested `modrinth_project_id`;
- `HINT`: one item matches only by declared or display identity;
- `AMBIGUOUS`: multiple provenance entries or hints match;
- `NONE`: there is no usable match.

Only `OWNED` may authorize replacement, stale-file removal, installed-version comparison, or an
incompatible-installed conclusion. A `HINT` can improve display and discovery but never grants
ownership.

## JAR Inspection

`inspect_mod_jar()` is the single bounded metadata reader for:

- `fabric.mod.json`;
- `quilt.mod.json`;
- `META-INF/neoforge.mods.toml`;
- `META-INF/mods.toml`;
- `mcmod.info`.

It returns every descriptor declared by the selected metadata file and applies a one MiB metadata
limit by default. Installed-mod discovery projects the primary descriptor into its legacy
dictionary format, while compatibility analysis consumes all descriptors, dependencies,
`provides`, and conflicts.

Do not add another ad-hoc JSON, TOML, or line-based JAR parser. Extend the shared inspection result
and its characterization tests instead.
