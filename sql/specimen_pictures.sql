-- specimen_pictures: links specimens to Cloudinary image URLs per event.
-- Used by Bugbo ingest to store specimen images found in rocks/, water/, inat_images/.

CREATE TABLE IF NOT EXISTS public.specimen_pictures (
  id UUID PRIMARY KEY,
  specimen STRING NOT NULL,
  cloudinary_link STRING NOT NULL,
  event_name STRING NOT NULL,
  created_at TIMESTAMPTZ NULL DEFAULT now():::TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NULL DEFAULT now():::TIMESTAMPTZ,
  distractors STRING[]
);
