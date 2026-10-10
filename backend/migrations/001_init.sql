-- Liquid+ ingestion schema. Idempotent: safe to run repeatedly.
-- RLS is enabled on every table with NO policies, so only the backend's
-- database role (which bypasses RLS) can read or write. Supabase's public
-- anon / authenticated keys see nothing.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- batches
create table if not exists ingestion_batches (
    id              uuid primary key default gen_random_uuid(),
    book            text not null check (book in ('asset', 'liability', 'historical')),
    as_of_date      date not null,
    source_filename text not null,
    uploaded_by     text,
    status          text not null check (status in ('completed', 'failed')),
    rows_read       integer not null default 0,
    rows_valid      integer not null default 0,
    rows_rejected   integer not null default 0,
    warnings_count  integer not null default 0,
    duration_ms     integer not null default 0,
    is_active       boolean not null default true,
    created_at      timestamptz not null default now()
);
create index if not exists ix_batches_book_asof_active on ingestion_batches (book, as_of_date, is_active);
create index if not exists ix_batches_created on ingestion_batches (created_at desc);

-- ----------------------------------------------------------------- assets
create table if not exists assets (
    id                bigserial primary key,
    batch_id          uuid not null references ingestion_batches(id) on delete cascade,
    transaction_id    text not null check (transaction_id ~ '^AST-[0-9]+$'),
    entity            text not null check (entity in ('IBC HongKong','IBC USA','IBC UK','IBC Singapore','IBC SA')),
    location          text not null check (location in ('Hong Kong','New York','London','Singapore','Paris')),
    customer_id       text not null check (customer_id ~ '^CUS-[0-9]+$'),
    loan_amount       numeric(20,2) not null check (loan_amount > 0),
    currency          char(3) not null check (currency in ('USD','EUR','GBP','SGD','HKD')),
    transaction_date  date not null,
    maturity_date     date not null,
    interest_rate_pct numeric(9,4) not null check (interest_rate_pct between 0 and 100),
    market_rate_pct   numeric(9,4) not null check (market_rate_pct between 0 and 100),
    loan_type         text not null check (loan_type in ('Loan-Type-A','Loan-Type-B','Loan-Type-C','Loan-Type-D','Loan-Type-E')),
    customer_segment  text not null check (customer_segment in ('Retail','Corporate','SME','Institutional')),
    client_rating     smallint not null check (client_rating between 1 and 5),
    tenor_days        integer not null check (tenor_days >= 0),
    interest_amount   numeric(20,2) not null,
    cash_at_maturity  numeric(20,2) not null,
    unique (batch_id, transaction_id),
    check (maturity_date >= transaction_date),
    check ((entity = 'IBC HongKong' and location = 'Hong Kong') or (entity = 'IBC USA' and location = 'New York')
        or (entity = 'IBC UK' and location = 'London') or (entity = 'IBC Singapore' and location = 'Singapore')
        or (entity = 'IBC SA' and location = 'Paris'))
);
create index if not exists ix_assets_batch_ccy_loc on assets (batch_id, currency, location);
create index if not exists ix_assets_batch_maturity on assets (batch_id, maturity_date);

-- ------------------------------------------------------------ liabilities
create table if not exists liabilities (
    id                bigserial primary key,
    batch_id          uuid not null references ingestion_batches(id) on delete cascade,
    transaction_id    text not null check (transaction_id ~ '^LIA-[0-9]+$'),
    entity            text not null check (entity in ('IBC HongKong','IBC USA','IBC UK','IBC Singapore','IBC SA')),
    location          text not null check (location in ('Hong Kong','New York','London','Singapore','Paris')),
    customer_id       text not null check (customer_id ~ '^CUS-[0-9]+$'),
    amount            numeric(20,2) not null check (amount > 0),
    currency          char(3) not null check (currency in ('USD','EUR','GBP','SGD','HKD')),
    transaction_date  date not null,
    maturity_date     date not null,
    interest_rate_pct numeric(9,4) not null check (interest_rate_pct between 0 and 100),
    market_rate_pct   numeric(9,4) not null check (market_rate_pct between 0 and 100),
    product_type      text not null check (product_type in ('Product-A','Product-B','Product-C','Product-D','Product-E')),
    customer_segment  text not null check (customer_segment in ('Retail','Corporate','SME','Institutional')),
    client_rating     smallint not null check (client_rating between 1 and 5),
    tenor_days        integer not null check (tenor_days >= 0),
    interest_amount   numeric(20,2) not null,
    cash_at_maturity  numeric(20,2) not null,
    unique (batch_id, transaction_id),
    check (maturity_date >= transaction_date),
    check ((entity = 'IBC HongKong' and location = 'Hong Kong') or (entity = 'IBC USA' and location = 'New York')
        or (entity = 'IBC UK' and location = 'London') or (entity = 'IBC Singapore' and location = 'Singapore')
        or (entity = 'IBC SA' and location = 'Paris'))
);
create index if not exists ix_liabs_batch_ccy_loc on liabilities (batch_id, currency, location);
create index if not exists ix_liabs_batch_maturity on liabilities (batch_id, maturity_date);

-- -------------------------------------------------------- historical_log
-- No maturity >= transaction CHECK here: the use case defines no such rule
-- for this sheet, so those rows are stored with a warning.
create table if not exists historical_log (
    id                        bigserial primary key,
    batch_id                  uuid not null references ingestion_batches(id) on delete cascade,
    transaction_id            text not null check (transaction_id ~ '^(AST|LIA)-[0-9]+$'),
    type                      text not null check (type in ('Asset','Liability')),
    entity                    text not null check (entity in ('IBC HongKong','IBC USA','IBC UK','IBC Singapore','IBC SA')),
    location                  text not null check (location in ('Hong Kong','New York','London','Singapore','Paris')),
    customer_id               text not null check (customer_id ~ '^CUS-[0-9]+$'),
    amount                    numeric(20,2) not null check (amount > 0),
    currency                  char(3) not null check (currency in ('USD','EUR','GBP','SGD','HKD')),
    transaction_date          date not null,
    interest_rate_pct         numeric(9,4) not null check (interest_rate_pct between 0 and 100),
    market_rate_pct           numeric(9,4) not null check (market_rate_pct between 0 and 100),
    product_type              text not null check (product_type in (
                                  'Loan-Type-A','Loan-Type-B','Loan-Type-C','Loan-Type-D','Loan-Type-E',
                                  'Product-A','Product-B','Product-C','Product-D','Product-E')),
    customer_segment          text not null check (customer_segment in ('Retail','Corporate','SME','Institutional')),
    client_rating             smallint not null check (client_rating between 1 and 5),
    contractual_maturity_date date not null,
    actual_closure_date       date,
    is_early_exit             boolean,   -- NULL = still open; else closure < contractual maturity
    unique (batch_id, transaction_id),
    check ((entity = 'IBC HongKong' and location = 'Hong Kong') or (entity = 'IBC USA' and location = 'New York')
        or (entity = 'IBC UK' and location = 'London') or (entity = 'IBC Singapore' and location = 'Singapore')
        or (entity = 'IBC SA' and location = 'Paris'))
);
create index if not exists ix_hist_batch_type on historical_log (batch_id, type);
create index if not exists ix_hist_batch_closure on historical_log (batch_id, actual_closure_date);

-- ------------------------------------------------------ errors and audit
create table if not exists ingestion_errors (
    id             bigserial primary key,
    batch_id       uuid not null references ingestion_batches(id) on delete cascade,
    source_row     integer,                 -- row number as seen in Excel/CSV (header = row 1)
    transaction_id text,
    field          text,
    value          text,
    rule_code      text not null,
    message        text not null,
    severity       text not null check (severity in ('error', 'warning'))
);
create index if not exists ix_errors_batch on ingestion_errors (batch_id, severity, rule_code, source_row);

create table if not exists audit_log (
    id      bigserial primary key,
    ts      timestamptz not null default now(),
    actor   text,
    action  text not null,
    details jsonb
);
create index if not exists ix_audit_ts on audit_log (ts desc);

-- ------------------------------------------------------------------- RLS
alter table ingestion_batches enable row level security;
alter table assets            enable row level security;
alter table liabilities       enable row level security;
alter table historical_log    enable row level security;
alter table ingestion_errors  enable row level security;
alter table audit_log         enable row level security;
