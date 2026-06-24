docs(db): add onboarding viveristas migration script

For historical reference. Migration already applied to production
via Supabase MCP. Tests validated: trigger creates onboarding row
on mandate signature, no duplicates on subsequent UPDATEs.
