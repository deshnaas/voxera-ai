"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";

type Facility = {
  id: string;
  name: string;
  type: string;
  location: string;
  district: string;
  phone: string | null;
  operational_status: boolean;
};

type Bed = {
  id: string;
  facility_id: string;
  bed_type: string;
  total_beds: number;
  occupied_beds: number;
  updated_at: string;
};

type BedEdit = {
  total_beds: number;
  occupied_beds: number;
};

export default function BedsPage() {
  const [facility, setFacility] = useState<Facility | null>(null);
  const [beds, setBeds] = useState<Bed[]>([]);
  const [editValues, setEditValues] = useState<
    Record<string, BedEdit>
  >({});
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);

  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  useEffect(() => {
    loadBeds();
  }, []);

  async function loadBeds() {
    setLoading(true);
    setMessage("");

    try {
      const {
        data: { user },
        error: userError,
      } = await supabase.auth.getUser();

      if (userError || !user) {
        setMessage("You are not logged in.");
        setLoading(false);
        return;
      }

      const {
        data: hospitalUser,
        error: hospitalUserError,
      } = await supabase
        .from("hospital_users")
        .select("facility_id")
        .eq("user_id", user.id)
        .single();

      if (hospitalUserError || !hospitalUser) {
        setMessage("Hospital information could not be found.");
        setLoading(false);
        return;
      }

      const facilityId = hospitalUser.facility_id;

      const {
        data: facilityData,
        error: facilityError,
      } = await supabase
        .from("facilities")
        .select(
          `
            id,
            name,
            type,
            location,
            district,
            phone,
            operational_status
          `
        )
        .eq("id", facilityId)
        .single();

      if (facilityError || !facilityData) {
        setMessage(
          `Hospital details could not be loaded: ${
            facilityError?.message ?? "Unknown error"
          }`
        );
        setLoading(false);
        return;
      }

      setFacility(facilityData as Facility);

      const {
        data: bedData,
        error: bedError,
      } = await supabase
        .from("facility_beds")
        .select(
          `
            id,
            facility_id,
            bed_type,
            total_beds,
            occupied_beds,
            updated_at
          `
        )
        .eq("facility_id", facilityId)
        .order("bed_type", {
          ascending: true,
        });

      if (bedError) {
        setMessage(
          `Bed information could not be loaded: ${bedError.message}`
        );
        setLoading(false);
        return;
      }

      const loadedBeds = (bedData ?? []) as Bed[];

      setBeds(loadedBeds);

      const values: Record<string, BedEdit> = {};

      loadedBeds.forEach((bed) => {
        values[bed.id] = {
          total_beds: bed.total_beds,
          occupied_beds: bed.occupied_beds,
        };
      });

      setEditValues(values);
    } catch (error) {
      console.error(error);
      setMessage("Something went wrong while loading bed information.");
    } finally {
      setLoading(false);
    }
  }

  function updateEditValue(
    bedId: string,
    field: "total_beds" | "occupied_beds",
    value: string
  ) {
    const numericValue = Number(value);

    setEditValues((current) => ({
      ...current,
      [bedId]: {
        ...current[bedId],
        [field]: Number.isNaN(numericValue)
          ? 0
          : Math.max(0, numericValue),
      },
    }));
  }

  function cancelEditing() {
    const values: Record<string, BedEdit> = {};

    beds.forEach((bed) => {
      values[bed.id] = {
        total_beds: bed.total_beds,
        occupied_beds: bed.occupied_beds,
      };
    });

    setEditValues(values);
    setEditing(false);
    setMessage("");
  }

  async function saveBeds() {
    setMessage("");

    // --------------------------------------------------
    // Validate all bed values before saving
    // --------------------------------------------------

    for (const bed of beds) {
      const values = editValues[bed.id];

      if (!values) {
        setMessage("Bed information is incomplete.");
        return;
      }

      if (
        !Number.isInteger(values.total_beds) ||
        !Number.isInteger(values.occupied_beds)
      ) {
        setMessage("Bed numbers must be whole numbers.");
        return;
      }

      if (values.total_beds < 0) {
        setMessage("Total beds cannot be negative.");
        return;
      }

      if (values.occupied_beds < 0) {
        setMessage("Occupied beds cannot be negative.");
        return;
      }

      if (values.occupied_beds > values.total_beds) {
        setMessage(
          `${bed.bed_type}: occupied beds cannot be greater than total beds.`
        );
        return;
      }
    }

    setSaving(true);

    try {
      // --------------------------------------------------
      // Save each bed category
      // --------------------------------------------------

      for (const bed of beds) {
        const values = editValues[bed.id];

        const { error } = await supabase
          .from("facility_beds")
          .update({
            total_beds: values.total_beds,
            occupied_beds: values.occupied_beds,
            updated_at: new Date().toISOString(),
          })
          .eq("id", bed.id)
          .eq("facility_id", bed.facility_id);

        if (error) {
          setMessage(
            `Could not update ${bed.bed_type} beds: ${error.message}`
          );
          setSaving(false);
          return;
        }
      }

      setEditing(false);

      setMessage("Bed information updated successfully.");

      await loadBeds();
    } catch (error) {
      console.error(error);

      setMessage(
        "Something went wrong while updating bed information."
      );
    } finally {
      setSaving(false);
    }
  }

  // --------------------------------------------------
  // Calculations
  // --------------------------------------------------

  const totalBeds = beds.reduce(
    (sum, bed) => sum + bed.total_beds,
    0
  );

  const occupiedBeds = beds.reduce(
    (sum, bed) => sum + bed.occupied_beds,
    0
  );

  const availableBeds = totalBeds - occupiedBeds;

  const occupancyPercentage =
    totalBeds > 0
      ? Math.round((occupiedBeds / totalBeds) * 100)
      : 0;

  function availabilityLabel(available: number) {
    if (available <= 0) {
      return "Full";
    }

    if (available <= 3) {
      return "Low Availability";
    }

    return "Available";
  }

  function availabilityClass(available: number) {
    if (available <= 0) {
      return "border-red-500 text-red-600";
    }

    if (available <= 3) {
      return "border-orange-500 text-orange-600";
    }

    return "border-green-500 text-green-600";
  }

  function formatDateTime(value: string) {
    return new Intl.DateTimeFormat("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  }

  if (loading) {
    return (
      <main className="dashboard-page flex min-h-screen items-center justify-center p-8">
        <p className="text-lg font-semibold">
          Loading bed information...
        </p>
      </main>
    );
  }

  return (
    <main className="dashboard-page min-h-screen p-4 sm:p-8">
      <div className="mx-auto max-w-7xl">

        {/* Header */}
        <section className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-8">
          <div className="flex flex-col gap-5 md:flex-row md:items-center md:justify-between">

            <div>
              <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
                Hospital Operations
              </p>

              <h1 className="mt-2 text-4xl font-bold">
                🛏️ Beds
              </h1>

              <p className="dashboard-muted mt-2 text-sm">
                Monitor and update current hospital bed capacity.
              </p>
            </div>

            <div className="flex flex-wrap gap-3">

              <button
                type="button"
                onClick={loadBeds}
                disabled={saving}
                className="dashboard-hover-glow rounded-lg border-2 border-[#D4AF37] px-5 py-3 text-sm font-bold transition-all hover:bg-[#D4AF37] hover:text-black disabled:cursor-not-allowed disabled:opacity-50"
              >
                Refresh
              </button>

              {!editing ? (
                <button
                  type="button"
                  onClick={() => {
                    setMessage("");
                    setEditing(true);
                  }}
                  className="dashboard-hover-glow rounded-lg border-2 border-[#D4AF37] bg-[#D4AF37] px-5 py-3 text-sm font-bold text-black transition-all hover:shadow-[0_0_18px_rgba(212,175,55,0.45)]"
                >
                  Edit Beds
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={cancelEditing}
                    disabled={saving}
                    className="rounded-lg border-2 border-gray-400 px-5 py-3 text-sm font-bold transition-all hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Cancel
                  </button>

                  <button
                    type="button"
                    onClick={saveBeds}
                    disabled={saving}
                    className="dashboard-hover-glow rounded-lg border-2 border-[#D4AF37] bg-[#D4AF37] px-5 py-3 text-sm font-bold text-black transition-all hover:shadow-[0_0_18px_rgba(212,175,55,0.45)] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {saving ? "Saving..." : "Save Changes"}
                  </button>
                </>
              )}
            </div>
          </div>
        </section>

        {/* Message */}
        {message && (
          <div className="mt-5 rounded-xl border-2 border-[#D4AF37] bg-white px-5 py-4 text-sm font-semibold">
            {message}
          </div>
        )}

        {/* Hospital Information */}
        {facility && (
          <section className="dashboard-panel mt-8 rounded-2xl border-2 border-[#D4AF37] p-8">
            <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
              Facility
            </p>

            <h2 className="mt-2 text-2xl font-bold">
              {facility.name}
            </h2>

            <p className="dashboard-muted mt-2 text-sm">
              {facility.type} • {facility.location},{" "}
              {facility.district}
            </p>

            <div className="mt-3">
              <span
                className={`rounded-full border-2 px-3 py-1 text-xs font-bold uppercase ${
                  facility.operational_status
                    ? "border-green-500 text-green-600"
                    : "border-red-500 text-red-600"
                }`}
              >
                {facility.operational_status
                  ? "Operational"
                  : "Not Operational"}
              </span>
            </div>
          </section>
        )}

        {/* Summary */}
        <section className="mt-8 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">

          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Total Beds
            </p>

            <p className="mt-3 text-4xl font-bold">
              {totalBeds}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Hospital capacity
            </p>
          </div>

          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Occupied
            </p>

            <p className="mt-3 text-4xl font-bold">
              {occupiedBeds}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Currently occupied
            </p>
          </div>

          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Available
            </p>

            <p className="mt-3 text-4xl font-bold">
              {availableBeds}
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Beds currently available
            </p>
          </div>

          <div className="dashboard-panel dashboard-hover-glow rounded-2xl border-2 border-[#D4AF37] p-6">
            <p className="dashboard-muted text-sm font-semibold">
              Occupancy
            </p>

            <p className="mt-3 text-4xl font-bold">
              {occupancyPercentage}%
            </p>

            <p className="dashboard-muted mt-2 text-sm">
              Current occupancy rate
            </p>
          </div>
        </section>

        {/* Bed Categories */}
        <section className="dashboard-panel mt-8 rounded-2xl border-2 border-[#D4AF37] p-8">

          <div>
            <p className="dashboard-muted text-sm font-semibold uppercase tracking-widest">
              Bed Capacity
            </p>

            <h2 className="mt-2 text-2xl font-bold">
              Bed Availability by Type
            </h2>

            <p className="dashboard-muted mt-2 text-sm">
              {editing
                ? "Update total and occupied beds for your hospital."
                : "Current capacity reported by this hospital."}
            </p>
          </div>

          {beds.length === 0 ? (
            <div className="dashboard-subtle mt-6 rounded-xl border border-gray-300 p-8 text-center">
              <div className="text-5xl">
                🛏️
              </div>

              <h3 className="mt-4 text-xl font-bold">
                No bed information
              </h3>

              <p className="dashboard-muted mt-2 text-sm">
                No bed capacity records have been configured
                for this hospital.
              </p>
            </div>
          ) : (
            <div className="mt-6 grid gap-5 md:grid-cols-2 lg:grid-cols-3">

              {beds.map((bed) => {
                const values = editValues[bed.id] ?? {
                  total_beds: bed.total_beds,
                  occupied_beds: bed.occupied_beds,
                };

                const available =
                  bed.total_beds - bed.occupied_beds;

                const percentage =
                  bed.total_beds > 0
                    ? Math.round(
                        (bed.occupied_beds /
                          bed.total_beds) *
                          100
                      )
                    : 0;

                return (
                  <div
                    key={bed.id}
                    className="dashboard-hover-glow rounded-2xl border border-gray-300 p-6"
                  >

                    {/* Bed header */}
                    <div className="flex items-start justify-between gap-4">

                      <div>
                        <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                          Bed Type
                        </p>

                        <h3 className="mt-2 text-xl font-bold capitalize">
                          {bed.bed_type}
                        </h3>
                      </div>

                      {!editing && (
                        <span
                          className={`rounded-full border-2 px-3 py-1 text-xs font-bold uppercase ${availabilityClass(
                            available
                          )}`}
                        >
                          {availabilityLabel(available)}
                        </span>
                      )}
                    </div>

                    {editing ? (
                      /* ------------------------------------
                         EDIT MODE
                      ------------------------------------ */

                      <div className="mt-6 space-y-5">

                        <div>
                          <label
                            htmlFor={`total-${bed.id}`}
                            className="text-sm font-semibold"
                          >
                            Total Beds
                          </label>

                          <input
                            id={`total-${bed.id}`}
                            type="number"
                            min="0"
                            step="1"
                            value={values.total_beds}
                            onChange={(event) =>
                              updateEditValue(
                                bed.id,
                                "total_beds",
                                event.target.value
                              )
                            }
                            className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-3 py-3 text-sm font-bold text-black outline-none focus:shadow-[0_0_15px_rgba(212,175,55,0.4)]"
                          />
                        </div>

                        <div>
                          <label
                            htmlFor={`occupied-${bed.id}`}
                            className="text-sm font-semibold"
                          >
                            Occupied Beds
                          </label>

                          <input
                            id={`occupied-${bed.id}`}
                            type="number"
                            min="0"
                            max={values.total_beds}
                            step="1"
                            value={values.occupied_beds}
                            onChange={(event) =>
                              updateEditValue(
                                bed.id,
                                "occupied_beds",
                                event.target.value
                              )
                            }
                            className="mt-2 w-full rounded-lg border-2 border-[#D4AF37] bg-white px-3 py-3 text-sm font-bold text-black outline-none focus:shadow-[0_0_15px_rgba(212,175,55,0.4)]"
                          />
                        </div>

                        <div className="dashboard-subtle rounded-xl p-4">
                          <p className="dashboard-muted text-xs font-semibold uppercase tracking-wider">
                            Available Beds
                          </p>

                          <p className="mt-2 text-3xl font-bold">
                            {Math.max(
                              0,
                              values.total_beds -
                                values.occupied_beds
                            )}
                          </p>
                        </div>
                      </div>
                    ) : (
                      /* ------------------------------------
                         VIEW MODE
                      ------------------------------------ */

                      <>
                        <div className="mt-6 grid grid-cols-3 gap-3">

                          <div className="dashboard-subtle rounded-xl p-4 text-center">
                            <p className="dashboard-muted text-xs font-semibold">
                              Total
                            </p>

                            <p className="mt-2 text-2xl font-bold">
                              {bed.total_beds}
                            </p>
                          </div>

                          <div className="dashboard-subtle rounded-xl p-4 text-center">
                            <p className="dashboard-muted text-xs font-semibold">
                              Occupied
                            </p>

                            <p className="mt-2 text-2xl font-bold">
                              {bed.occupied_beds}
                            </p>
                          </div>

                          <div className="dashboard-subtle rounded-xl p-4 text-center">
                            <p className="dashboard-muted text-xs font-semibold">
                              Free
                            </p>

                            <p className="mt-2 text-2xl font-bold">
                              {available}
                            </p>
                          </div>

                        </div>

                        <div className="mt-6">
                          <div className="flex items-center justify-between text-xs font-semibold">
                            <span className="dashboard-muted">
                              Occupancy
                            </span>

                            <span>
                              {percentage}%
                            </span>
                          </div>

                          <div className="mt-2 h-3 overflow-hidden rounded-full bg-gray-200">
                            <div
                              className="h-full rounded-full bg-[#D4AF37] transition-all"
                              style={{
                                width: `${Math.min(
                                  percentage,
                                  100
                                )}%`,
                              }}
                            />
                          </div>
                        </div>

                        <p className="dashboard-muted mt-5 text-xs">
                          Last updated:{" "}
                          {formatDateTime(
                            bed.updated_at
                          )}
                        </p>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* Information */}
        <section className="dashboard-subtle mt-8 rounded-2xl border border-gray-300 p-6">
          <h3 className="font-bold">
            🏥 Capacity Information
          </h3>

          <p className="dashboard-muted mt-2 text-sm leading-6">
            Hospital staff can update their own facility's
            bed capacity. Available beds are calculated
            automatically from total beds minus occupied beds.
            Occupied beds can never exceed total beds.
          </p>
        </section>
      </div>
    </main>
  );
}