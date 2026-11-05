# Seed data format

All seed files are UTF-8 JSON. Persian text uses proper ZWNJ (نیم‌فاصله, U+200C), e.g. «می‌خوریم», «گوجه‌فرنگی».
Numbers inside JSON are plain ASCII numbers; the UI converts them to Persian digits.

## `ingredients.json` — array of

```jsonc
{
  "key": "onion", // canonical_name: unique, lowercase snake_case English, singular
  "name_fa": "پیاز",
  "aliases_fa": ["پیاز زرد"], // other Persian names users may type (may be [])
  "category": "produce", // shopping aisle, one of:
  //   produce | dairy | meat | bakery | grains_legumes | spices
  //   | canned | frozen | drinks | other   (oils, sauces, sweets, nuts…)
  "default_unit": "piece", // one of: g | ml | piece | clove | slice | bunch | tbsp | tsp | cup | pinch | can
  "grams_per_unit": { "piece": 150, "cup": 160 }, // grams for each NON-g unit this ingredient is used with
  // (for liquids give ml too, e.g. milk: {"ml": 1.03, "cup": 245})
  "nutrition_per_100g": { "kcal": 40, "protein": 1.1, "carbs": 9.3, "fat": 0.1, "fiber": 1.7 },
  "shelf_life_days": { "fridge": 30, "freezer": 240, "pantry": 30, "after_opening_fridge": null },
  // null = not applicable / not recommended in that place
  "freezable": true,
  "flags": [], // zero or more of:
  //   red_meat | pork | poultry | seafood | processed_meat
  //   | dairy | lactose | gluten | nuts | egg | soy | alcohol
  //   | legume | sweet | spicy | animal_product (anything non-vegan, incl. honey)
}
```

Rules: meat/poultry/seafood/dairy/egg/honey MUST also carry `animal_product`.
Beef, lamb, veal and their mince/sausages carry `red_meat`. Wheat flour, bread, pasta, lavash,
bulgur, couscous carry `gluten`. Milk, yogurt, cream, cheese, butter carry `dairy`
(and `lactose`, except hard aged cheeses which may omit it).

## `recipes.json` — array of

```jsonc
{
  "key": "shakshuka", // unique snake_case
  "title_fa": "شکشوکا",
  "title_original": "Shakshuka", // original-language title (may equal title_fa for Iranian food)
  "intro_fa": "یه جمله‌ی تنبلانه و خودمونی.",
  "cuisine": "other", // iranian | turkish | armenian | greek | italian | mexican | indian | asian
  // | spanish | french | american | other
  "meal_types": ["breakfast", "dinner"], // subset of breakfast | lunch | dinner | snack | dessert | drink
  "base_servings": 2,
  "active_minutes": 15,
  "total_minutes": 25,
  "difficulty": 1, // 1..3
  "requires_equipment": [], // subset of oven | microwave | airfryer | pressure_cooker | blender
  //   | mixer | sandwich_maker | toaster | freezer
  "equipment_alternatives_fa": null, // e.g. "بدون فر: در تابه‌ی دربسته روی شعله‌ی کم ۲۰ دقیقه" (string or null)
  "ingredients": [
    {
      "ingredient": "egg",
      "quantity": 4,
      "unit": "piece",
      "prep_note": null,
      "optional": false,
      "group": null,
    },
  ], // "ingredient" = key from ingredients.json; unit from the unit list above
  "steps": [{ "text": "یک جمله‌ی کوتاه.", "timer_seconds": 300 }], // MAX 7 steps; timer_seconds optional
  "nutrition_per_serving": { "kcal": 320, "protein": 16, "carbs": 18, "fat": 20, "fiber": 4 },
  "freezer_friendly": false,
  "fridge_days": 2,
  "reheat_note": "در تابه با یه قاشق آب گرم کن.", // or null
  "prep_ahead": [{ "text": "لوبیا را از شب قبل خیس کن.", "hours_before": 12 }], // [] if none
  "tags": ["one_pot", "ten_minute"], // subset of: one_pot | no_cook | ten_minute | high_protein | vegetarian
  //   | vegan | low_red_meat | light | freezer | leftover_friendly
  //   | budget | no_oven | sweet | iranian_classic
  "source": "seed",
  "source_credit": null,
}
```
