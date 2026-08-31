# Supabase migration workflow

BirdBrain now uses Supabase's standard migration directory: `supabase/migrations/`.

## Important: initial schema was applied manually

The development project already received the initial BirdBrain schema through the Supabase SQL Editor before this directory was created.

Before the first `supabase db push`, link the local repository to the development project and mark the initial migration as already applied:

```bash
supabase login
supabase link --project-ref <YOUR_DEV_PROJECT_REF>
supabase migration repair --status applied 20260831161552
```

Then verify migration state:

```bash
supabase migration list
```

The first unapplied migration should be:

```text
20260831162500_add_migration_staging.sql
```

Preview it before applying:

```bash
supabase db push --dry-run
```

Then apply it:

```bash
supabase db push
```

After this point, do not make routine schema changes directly in the remote SQL Editor or Table Editor. Add migration files to `supabase/migrations/` and deploy them through the migration workflow.

## Safety

- Link only the BirdBrain development project while this migration is under construction.
- Never commit the database password, service-role key, `.env`, or `.Renviron`.
- Never run `supabase db reset --linked` against a production project.
- The live Google Sheet remains read-only during migration.
