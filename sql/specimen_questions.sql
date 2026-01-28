 CREATE TABLE public.specimen_questions (
    id UUID NOT NULL,
    question STRING NOT NULL,
    tournament STRING NOT NULL,
    division STRING NOT NULL,
    options JSONB NULL DEFAULT '[]':::JSONB,
    answers JSONB NOT NULL,
    subtopics JSONB NULL DEFAULT '[]':::JSONB,
    difficulty DECIMAL NULL DEFAULT 0.5:::DECIMAL,
    event STRING NOT NULL,
    random_f FLOAT8 NULL DEFAULT random(),
    created_at TIMESTAMPTZ NULL DEFAULT now():::TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NULL DEFAULT now():::TIMESTAMPTZ,
    question_type STRING NULL AS (
      CASE
        WHEN (jsonb_typeof(options) = 'array':::STRING)
         AND (jsonb_array_length(options) >= 2:::INT8)
        THEN 'mcq':::STRING
        ELSE 'frq':::STRING
      END
    ) STORED,
    pure_id BOOL NULL DEFAULT false,
    rm_type STRING NULL,
    specimen STRING NOT NULL,
    statesNationals BOOL NOT NULL DEFAULT false
  );
