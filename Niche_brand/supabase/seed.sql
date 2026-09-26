insert into public.brands (
  name,
  category,
  profile_url,
  post_url,
  followers,
  engagement_rate,
  niche_score,
  trend_score
)
values
  ('sams.aesthetic.store', 'Accessories', 'https://www.instagram.com/sams.aesthetic.store/', 'https://www.instagram.com/p/DUKyII8jLwJ/', 6152, 0, 1.0, 0),
  ('bowberry.in', 'Accessories', 'https://www.instagram.com/bowberry.in/', 'https://www.instagram.com/p/DREbBcAjOnk/', 20000, 0, 1.0, 0),
  ('niceg.co', 'Accessories', 'https://www.instagram.com/niceg.co/', 'https://www.instagram.com/p/DRNB_m-kxE4/', 10000, 0, 1.0, 0),
  ('wearwither', 'Clothing', 'https://www.instagram.com/wearwither/', 'https://www.instagram.com/p/DVLse3EEx0D/', 25000, 0, 1.0, 0),
  ('kai.by.mangi', 'Accessories', 'https://www.instagram.com/kai.by.mangi/', 'https://www.instagram.com/p/DT7rY4RDKoK/', 7956, 0, 1.0, 0),
  ('shadesofshine.in', 'Accessories', 'https://www.instagram.com/shadesofshine.in/', 'https://www.instagram.com/p/DTvY8pPDDyA/', 3339, 0, 1.0, 0),
  ('farmkin.in', 'House & Furnishing', 'https://www.instagram.com/farmkin.in/', 'https://www.instagram.com/p/DTu7W1agTmC/', 2171, 0, 1.0, 0),
  ('toolboxraw', 'Accessories', 'https://www.instagram.com/toolboxraw/', 'https://www.instagram.com/p/DWBtRbbEn84/', 5291, 0, 1.0, 0),
  ('_dreamy_haven', 'Clothing', 'https://www.instagram.com/_dreamy_haven/', 'https://www.instagram.com/p/DTnOAoqkojE/', 5041, 0, 1.0, 0),
  ('littlestore_corner', 'Clothing', 'https://www.instagram.com/littlestore_corner/', 'https://www.instagram.com/p/DVS02OBEUTU/', 6899, 0, 1.0, 0),
  ('outfits_n_moreee', 'Clothing', 'https://www.instagram.com/outfits_n_moreee/', 'https://www.instagram.com/p/DUqs3p1DOFw/', 7371, 0, 1.0, 0),
  ('klane.in', 'Clothing', 'https://www.instagram.com/klane.in/', 'https://www.instagram.com/p/DLAlNe4hLRH/', 16100, 0, 1.0, 0),
  ('swordrobe_', 'Clothing', 'https://www.instagram.com/swordrobe_/', 'https://www.instagram.com/p/DTu6rH0CPx7/', 12300, 0, 1.0, 0),
  ('artees.corner', 'Clothing', 'https://www.instagram.com/artees.corner/', 'https://www.instagram.com/p/DV1QW9qSO4N/', 6811, 0, 1.0, 0),
  ('titlqe', 'Clothing', 'https://www.instagram.com/titlqe/', 'https://www.instagram.com/p/DPl14meDBG4/', 43200, 0, 1.0, 0),
  ('dripmatrix', 'Clothing', 'https://www.instagram.com/dripmatrix/', 'https://www.instagram.com/p/DU-VauDk2MS/', 4882, 0, 1.0, 0),
  ('israayaindiaofficial', 'Clothing', 'https://www.instagram.com/israayaindiaofficial/', 'https://www.instagram.com/p/DU7vUyjD0Jc/', 96, 0, 0.8, 0),
  ('state.of.mitch', 'Clothing', 'https://www.instagram.com/state.of.mitch/', 'https://www.instagram.com/p/DM7ggofzBNF/', 752, 0, 0.8, 0),
  ('the.bloom.closet', 'Clothing', 'https://www.instagram.com/the.bloom.closet/', 'https://www.instagram.com/p/DHiM5ZvvZSs/', 4915, 0, 1.0, 0)
on conflict (name) do update set
  category = excluded.category,
  profile_url = excluded.profile_url,
  post_url = excluded.post_url,
  followers = excluded.followers,
  engagement_rate = excluded.engagement_rate,
  niche_score = excluded.niche_score,
  trend_score = excluded.trend_score;
