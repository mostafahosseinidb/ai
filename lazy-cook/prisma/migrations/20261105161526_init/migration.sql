-- CreateEnum
CREATE TYPE "CalendarKind" AS ENUM ('jalali', 'gregorian');

-- CreateEnum
CREATE TYPE "UnitSystem" AS ENUM ('metric', 'cups');

-- CreateEnum
CREATE TYPE "HouseholdRole" AS ENUM ('owner', 'member');

-- CreateEnum
CREATE TYPE "Goal" AS ENUM ('maintain', 'lose_slow', 'lose', 'gain_muscle');

-- CreateEnum
CREATE TYPE "ActivityLevel" AS ENUM ('sedentary', 'light', 'moderate', 'active');

-- CreateEnum
CREATE TYPE "Sex" AS ENUM ('female', 'male', 'other');

-- CreateEnum
CREATE TYPE "PreferenceType" AS ENUM ('diet', 'avoid', 'limit', 'dislike', 'like', 'intolerance');

-- CreateEnum
CREATE TYPE "RecipeSource" AS ENUM ('seed', 'ai', 'user_import', 'user_manual');

-- CreateEnum
CREATE TYPE "PlanStatus" AS ENUM ('draft', 'active', 'completed', 'archived');

-- CreateEnum
CREATE TYPE "MealStatus" AS ENUM ('planned', 'done', 'skipped', 'eaten_out', 'replaced');

-- CreateEnum
CREATE TYPE "Rating" AS ENUM ('up', 'down');

-- CreateEnum
CREATE TYPE "StorageLocation" AS ENUM ('fridge', 'freezer', 'pantry');

-- CreateEnum
CREATE TYPE "PantryStatus" AS ENUM ('active', 'used', 'discarded');

-- CreateEnum
CREATE TYPE "ShoppingSection" AS ENUM ('required', 'optional');

-- CreateEnum
CREATE TYPE "AddedBy" AS ENUM ('auto', 'user', 'assistant');

-- CreateEnum
CREATE TYPE "Actor" AS ENUM ('user', 'assistant', 'system');

-- CreateEnum
CREATE TYPE "LocalNameSource" AS ENUM ('seed', 'ai', 'user');

-- CreateTable
CREATE TABLE "users" (
    "id" UUID NOT NULL,
    "email" TEXT,
    "emailVerified" TIMESTAMP(3),
    "name" TEXT,
    "image" TEXT,
    "locale" TEXT NOT NULL DEFAULT 'fa',
    "countryCode" VARCHAR(2),
    "city" TEXT,
    "timezone" TEXT NOT NULL DEFAULT 'UTC',
    "currency" VARCHAR(3),
    "calendar" "CalendarKind" NOT NULL DEFAULT 'jalali',
    "units" "UnitSystem" NOT NULL DEFAULT 'metric',
    "subscriptionStatus" TEXT NOT NULL DEFAULT 'free',
    "onboardingCompletedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "users_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "accounts" (
    "id" UUID NOT NULL,
    "userId" UUID NOT NULL,
    "type" TEXT NOT NULL,
    "provider" TEXT NOT NULL,
    "providerAccountId" TEXT NOT NULL,
    "refresh_token" TEXT,
    "access_token" TEXT,
    "expires_at" INTEGER,
    "token_type" TEXT,
    "scope" TEXT,
    "id_token" TEXT,
    "session_state" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "accounts_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "sessions" (
    "id" UUID NOT NULL,
    "sessionToken" TEXT NOT NULL,
    "userId" UUID NOT NULL,
    "expires" TIMESTAMP(3) NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "sessions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "verification_tokens" (
    "identifier" TEXT NOT NULL,
    "token" TEXT NOT NULL,
    "expires" TIMESTAMP(3) NOT NULL
);

-- CreateTable
CREATE TABLE "households" (
    "id" UUID NOT NULL,
    "name" TEXT NOT NULL,
    "ownerUserId" UUID NOT NULL,
    "cookingDaysPerWeek" INTEGER NOT NULL DEFAULT 4,
    "batchPrepWeekday" INTEGER,
    "iranianFoodFrequencyDays" INTEGER NOT NULL DEFAULT 7,
    "weeklyBudget" DECIMAL(10,2),
    "activeMealSlots" TEXT[] DEFAULT ARRAY['breakfast', 'lunch', 'dinner']::TEXT[],
    "bedtime" TEXT,
    "kitchenEquipment" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "burners" INTEGER NOT NULL DEFAULT 2,
    "autoDeductPantry" BOOLEAN NOT NULL DEFAULT true,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "households_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "household_users" (
    "id" UUID NOT NULL,
    "householdId" UUID NOT NULL,
    "userId" UUID NOT NULL,
    "role" "HouseholdRole" NOT NULL DEFAULT 'member',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "household_users_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "members" (
    "id" UUID NOT NULL,
    "householdId" UUID NOT NULL,
    "displayName" TEXT NOT NULL,
    "birthYear" INTEGER,
    "sex" "Sex",
    "heightCm" INTEGER,
    "weightKg" DECIMAL(5,1),
    "goal" "Goal" NOT NULL DEFAULT 'maintain',
    "activityLevel" "ActivityLevel" NOT NULL DEFAULT 'light',
    "portionFactor" DECIMAL(3,2) NOT NULL DEFAULT 1.0,
    "dailyKcalEstimate" INTEGER,
    "waterGoalMl" INTEGER,
    "position" INTEGER NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "members_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "member_preferences" (
    "id" UUID NOT NULL,
    "memberId" UUID NOT NULL,
    "type" "PreferenceType" NOT NULL,
    "value" TEXT NOT NULL,
    "limitPerWeek" INTEGER,
    "note" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "member_preferences_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ingredients" (
    "id" UUID NOT NULL,
    "canonicalName" TEXT NOT NULL,
    "nameFa" TEXT NOT NULL,
    "aliasesFa" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "category" TEXT NOT NULL,
    "defaultUnit" TEXT NOT NULL,
    "gramsPerUnit" JSONB NOT NULL DEFAULT '{}',
    "nutritionPer100g" JSONB NOT NULL,
    "shelfLifeDays" JSONB NOT NULL,
    "freezable" BOOLEAN NOT NULL DEFAULT false,
    "flags" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "ingredients_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ingredient_local_names" (
    "id" UUID NOT NULL,
    "ingredientId" UUID NOT NULL,
    "countryCode" VARCHAR(2) NOT NULL,
    "language" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "notes" TEXT,
    "source" "LocalNameSource" NOT NULL DEFAULT 'seed',
    "upvotes" INTEGER NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "ingredient_local_names_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "recipes" (
    "id" UUID NOT NULL,
    "slug" TEXT,
    "ownerHouseholdId" UUID,
    "titleFa" TEXT NOT NULL,
    "titleOriginal" TEXT,
    "introFa" TEXT,
    "cuisine" TEXT NOT NULL,
    "mealTypes" TEXT[],
    "baseServings" INTEGER NOT NULL DEFAULT 2,
    "activeMinutes" INTEGER NOT NULL,
    "totalMinutes" INTEGER NOT NULL,
    "difficulty" INTEGER NOT NULL DEFAULT 1,
    "requiresEquipment" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "equipmentAlternativesFa" TEXT,
    "steps" JSONB NOT NULL,
    "nutritionPerServing" JSONB,
    "freezerFriendly" BOOLEAN NOT NULL DEFAULT false,
    "fridgeDays" INTEGER,
    "reheatNote" TEXT,
    "prepAhead" JSONB NOT NULL DEFAULT '[]',
    "tags" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "source" "RecipeSource" NOT NULL DEFAULT 'user_manual',
    "sourceCredit" TEXT,
    "parentRecipeId" UUID,
    "photoUrl" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "recipes_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "recipe_ingredients" (
    "id" UUID NOT NULL,
    "recipeId" UUID NOT NULL,
    "ingredientId" UUID NOT NULL,
    "quantity" DECIMAL(10,3) NOT NULL,
    "unit" TEXT NOT NULL,
    "prepNote" TEXT,
    "optional" BOOLEAN NOT NULL DEFAULT false,
    "group" TEXT,
    "position" INTEGER NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "recipe_ingredients_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "meal_plans" (
    "id" UUID NOT NULL,
    "householdId" UUID NOT NULL,
    "startDate" DATE NOT NULL,
    "days" INTEGER NOT NULL,
    "status" "PlanStatus" NOT NULL DEFAULT 'draft',
    "generationInput" JSONB,
    "aiCallId" UUID,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "meal_plans_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "planned_meals" (
    "id" UUID NOT NULL,
    "mealPlanId" UUID NOT NULL,
    "date" DATE NOT NULL,
    "slot" TEXT NOT NULL,
    "recipeId" UUID,
    "freeText" TEXT,
    "servingsByMember" JSONB NOT NULL DEFAULT '{}',
    "leftoverOfPlannedMealId" UUID,
    "fromFreezerPantryItemId" UUID,
    "side" TEXT,
    "drink" TEXT,
    "memberNotes" JSONB NOT NULL DEFAULT '{}',
    "tag" TEXT,
    "status" "MealStatus" NOT NULL DEFAULT 'planned',
    "eatenOutText" TEXT,
    "doneAt" TIMESTAMP(3),
    "position" INTEGER NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "planned_meals_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "meal_feedback" (
    "id" UUID NOT NULL,
    "plannedMealId" UUID NOT NULL,
    "memberId" UUID,
    "rating" "Rating" NOT NULL,
    "reasons" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "comment" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "meal_feedback_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "free_logs" (
    "id" UUID NOT NULL,
    "householdId" UUID NOT NULL,
    "date" DATE NOT NULL,
    "slot" TEXT,
    "text" TEXT NOT NULL,
    "estimatedNutrition" JSONB,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "free_logs_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "prep_tasks" (
    "id" UUID NOT NULL,
    "householdId" UUID NOT NULL,
    "mealPlanId" UUID,
    "dueDate" DATE NOT NULL,
    "dueTime" TEXT NOT NULL DEFAULT '21:00',
    "text" TEXT NOT NULL,
    "relatedPlannedMealId" UUID,
    "doneAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "prep_tasks_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "pantry_items" (
    "id" UUID NOT NULL,
    "householdId" UUID NOT NULL,
    "ingredientId" UUID,
    "customName" TEXT,
    "recipeId" UUID,
    "quantity" DECIMAL(10,3) NOT NULL,
    "unit" TEXT NOT NULL,
    "location" "StorageLocation" NOT NULL,
    "purchasedAt" TIMESTAMP(3),
    "openedAt" TIMESTAMP(3),
    "packageExpiryDate" DATE,
    "computedExpiryDate" DATE,
    "status" "PantryStatus" NOT NULL DEFAULT 'active',
    "note" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "pantry_items_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "shopping_items" (
    "id" UUID NOT NULL,
    "householdId" UUID NOT NULL,
    "mealPlanId" UUID,
    "ingredientId" UUID,
    "customName" TEXT,
    "quantity" DECIMAL(10,3),
    "unit" TEXT,
    "packageHint" TEXT,
    "section" "ShoppingSection" NOT NULL DEFAULT 'required',
    "aisle" TEXT NOT NULL DEFAULT 'other',
    "checkedAt" TIMESTAMP(3),
    "addedBy" "AddedBy" NOT NULL DEFAULT 'auto',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "shopping_items_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "wishlist_items" (
    "id" UUID NOT NULL,
    "householdId" UUID NOT NULL,
    "recipeId" UUID NOT NULL,
    "note" TEXT,
    "priority" INTEGER NOT NULL DEFAULT 0,
    "scheduledPlannedMealId" UUID,
    "doneAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "wishlist_items_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "recurring_rules" (
    "id" UUID NOT NULL,
    "householdId" UUID NOT NULL,
    "recipeId" UUID NOT NULL,
    "everyDays" INTEGER NOT NULL,
    "preferredWeekday" INTEGER,
    "lastCookedOn" DATE,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "recurring_rules_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "assistant_messages" (
    "id" UUID NOT NULL,
    "householdId" UUID NOT NULL,
    "userId" UUID,
    "role" TEXT NOT NULL,
    "content" TEXT NOT NULL,
    "attachments" JSONB NOT NULL DEFAULT '[]',
    "proposedChanges" JSONB,
    "appliedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    "deletedAt" TIMESTAMP(3),

    CONSTRAINT "assistant_messages_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "change_log" (
    "id" UUID NOT NULL,
    "householdId" UUID NOT NULL,
    "batchId" UUID,
    "entity" TEXT NOT NULL,
    "entityId" UUID NOT NULL,
    "before" JSONB,
    "after" JSONB,
    "actor" "Actor" NOT NULL,
    "revertedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "change_log_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ai_calls" (
    "id" UUID NOT NULL,
    "householdId" UUID,
    "purpose" TEXT NOT NULL,
    "model" TEXT NOT NULL,
    "inputTokens" INTEGER NOT NULL DEFAULT 0,
    "outputTokens" INTEGER NOT NULL DEFAULT 0,
    "costUsd" DECIMAL(10,6) NOT NULL DEFAULT 0,
    "latencyMs" INTEGER NOT NULL DEFAULT 0,
    "success" BOOLEAN NOT NULL,
    "error" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "ai_calls_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "users_email_key" ON "users"("email");

-- CreateIndex
CREATE UNIQUE INDEX "accounts_provider_providerAccountId_key" ON "accounts"("provider", "providerAccountId");

-- CreateIndex
CREATE UNIQUE INDEX "sessions_sessionToken_key" ON "sessions"("sessionToken");

-- CreateIndex
CREATE UNIQUE INDEX "verification_tokens_identifier_token_key" ON "verification_tokens"("identifier", "token");

-- CreateIndex
CREATE UNIQUE INDEX "household_users_householdId_userId_key" ON "household_users"("householdId", "userId");

-- CreateIndex
CREATE INDEX "member_preferences_memberId_idx" ON "member_preferences"("memberId");

-- CreateIndex
CREATE UNIQUE INDEX "ingredients_canonicalName_key" ON "ingredients"("canonicalName");

-- CreateIndex
CREATE INDEX "ingredient_local_names_ingredientId_countryCode_idx" ON "ingredient_local_names"("ingredientId", "countryCode");

-- CreateIndex
CREATE UNIQUE INDEX "recipes_slug_key" ON "recipes"("slug");

-- CreateIndex
CREATE INDEX "recipes_ownerHouseholdId_idx" ON "recipes"("ownerHouseholdId");

-- CreateIndex
CREATE INDEX "recipe_ingredients_recipeId_idx" ON "recipe_ingredients"("recipeId");

-- CreateIndex
CREATE INDEX "meal_plans_householdId_status_idx" ON "meal_plans"("householdId", "status");

-- CreateIndex
CREATE INDEX "planned_meals_mealPlanId_date_idx" ON "planned_meals"("mealPlanId", "date");

-- CreateIndex
CREATE INDEX "meal_feedback_plannedMealId_idx" ON "meal_feedback"("plannedMealId");

-- CreateIndex
CREATE INDEX "free_logs_householdId_date_idx" ON "free_logs"("householdId", "date");

-- CreateIndex
CREATE INDEX "prep_tasks_householdId_dueDate_idx" ON "prep_tasks"("householdId", "dueDate");

-- CreateIndex
CREATE INDEX "pantry_items_householdId_status_idx" ON "pantry_items"("householdId", "status");

-- CreateIndex
CREATE INDEX "shopping_items_householdId_idx" ON "shopping_items"("householdId");

-- CreateIndex
CREATE INDEX "assistant_messages_householdId_createdAt_idx" ON "assistant_messages"("householdId", "createdAt");

-- CreateIndex
CREATE INDEX "change_log_householdId_createdAt_idx" ON "change_log"("householdId", "createdAt");

-- AddForeignKey
ALTER TABLE "accounts" ADD CONSTRAINT "accounts_userId_fkey" FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "sessions" ADD CONSTRAINT "sessions_userId_fkey" FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "households" ADD CONSTRAINT "households_ownerUserId_fkey" FOREIGN KEY ("ownerUserId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "household_users" ADD CONSTRAINT "household_users_householdId_fkey" FOREIGN KEY ("householdId") REFERENCES "households"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "household_users" ADD CONSTRAINT "household_users_userId_fkey" FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "members" ADD CONSTRAINT "members_householdId_fkey" FOREIGN KEY ("householdId") REFERENCES "households"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "member_preferences" ADD CONSTRAINT "member_preferences_memberId_fkey" FOREIGN KEY ("memberId") REFERENCES "members"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ingredient_local_names" ADD CONSTRAINT "ingredient_local_names_ingredientId_fkey" FOREIGN KEY ("ingredientId") REFERENCES "ingredients"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "recipes" ADD CONSTRAINT "recipes_ownerHouseholdId_fkey" FOREIGN KEY ("ownerHouseholdId") REFERENCES "households"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "recipes" ADD CONSTRAINT "recipes_parentRecipeId_fkey" FOREIGN KEY ("parentRecipeId") REFERENCES "recipes"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "recipe_ingredients" ADD CONSTRAINT "recipe_ingredients_recipeId_fkey" FOREIGN KEY ("recipeId") REFERENCES "recipes"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "recipe_ingredients" ADD CONSTRAINT "recipe_ingredients_ingredientId_fkey" FOREIGN KEY ("ingredientId") REFERENCES "ingredients"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "meal_plans" ADD CONSTRAINT "meal_plans_householdId_fkey" FOREIGN KEY ("householdId") REFERENCES "households"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "meal_plans" ADD CONSTRAINT "meal_plans_aiCallId_fkey" FOREIGN KEY ("aiCallId") REFERENCES "ai_calls"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "planned_meals" ADD CONSTRAINT "planned_meals_mealPlanId_fkey" FOREIGN KEY ("mealPlanId") REFERENCES "meal_plans"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "planned_meals" ADD CONSTRAINT "planned_meals_recipeId_fkey" FOREIGN KEY ("recipeId") REFERENCES "recipes"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "planned_meals" ADD CONSTRAINT "planned_meals_leftoverOfPlannedMealId_fkey" FOREIGN KEY ("leftoverOfPlannedMealId") REFERENCES "planned_meals"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "planned_meals" ADD CONSTRAINT "planned_meals_fromFreezerPantryItemId_fkey" FOREIGN KEY ("fromFreezerPantryItemId") REFERENCES "pantry_items"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "meal_feedback" ADD CONSTRAINT "meal_feedback_plannedMealId_fkey" FOREIGN KEY ("plannedMealId") REFERENCES "planned_meals"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "meal_feedback" ADD CONSTRAINT "meal_feedback_memberId_fkey" FOREIGN KEY ("memberId") REFERENCES "members"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "free_logs" ADD CONSTRAINT "free_logs_householdId_fkey" FOREIGN KEY ("householdId") REFERENCES "households"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "prep_tasks" ADD CONSTRAINT "prep_tasks_householdId_fkey" FOREIGN KEY ("householdId") REFERENCES "households"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "prep_tasks" ADD CONSTRAINT "prep_tasks_mealPlanId_fkey" FOREIGN KEY ("mealPlanId") REFERENCES "meal_plans"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "prep_tasks" ADD CONSTRAINT "prep_tasks_relatedPlannedMealId_fkey" FOREIGN KEY ("relatedPlannedMealId") REFERENCES "planned_meals"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "pantry_items" ADD CONSTRAINT "pantry_items_householdId_fkey" FOREIGN KEY ("householdId") REFERENCES "households"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "pantry_items" ADD CONSTRAINT "pantry_items_ingredientId_fkey" FOREIGN KEY ("ingredientId") REFERENCES "ingredients"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "pantry_items" ADD CONSTRAINT "pantry_items_recipeId_fkey" FOREIGN KEY ("recipeId") REFERENCES "recipes"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "shopping_items" ADD CONSTRAINT "shopping_items_householdId_fkey" FOREIGN KEY ("householdId") REFERENCES "households"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "shopping_items" ADD CONSTRAINT "shopping_items_mealPlanId_fkey" FOREIGN KEY ("mealPlanId") REFERENCES "meal_plans"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "shopping_items" ADD CONSTRAINT "shopping_items_ingredientId_fkey" FOREIGN KEY ("ingredientId") REFERENCES "ingredients"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "wishlist_items" ADD CONSTRAINT "wishlist_items_householdId_fkey" FOREIGN KEY ("householdId") REFERENCES "households"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "wishlist_items" ADD CONSTRAINT "wishlist_items_recipeId_fkey" FOREIGN KEY ("recipeId") REFERENCES "recipes"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "wishlist_items" ADD CONSTRAINT "wishlist_items_scheduledPlannedMealId_fkey" FOREIGN KEY ("scheduledPlannedMealId") REFERENCES "planned_meals"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "recurring_rules" ADD CONSTRAINT "recurring_rules_householdId_fkey" FOREIGN KEY ("householdId") REFERENCES "households"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "recurring_rules" ADD CONSTRAINT "recurring_rules_recipeId_fkey" FOREIGN KEY ("recipeId") REFERENCES "recipes"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "assistant_messages" ADD CONSTRAINT "assistant_messages_householdId_fkey" FOREIGN KEY ("householdId") REFERENCES "households"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "assistant_messages" ADD CONSTRAINT "assistant_messages_userId_fkey" FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "change_log" ADD CONSTRAINT "change_log_householdId_fkey" FOREIGN KEY ("householdId") REFERENCES "households"("id") ON DELETE CASCADE ON UPDATE CASCADE;
