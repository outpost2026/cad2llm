# Zone metadata
sel = Sketchup.active_model.selection.first
sel.set_attribute("zone", "type", "sleep")
sel.set_attribute("zone", "temp_setpoint_c", 21)
sel.set_attribute("zone", "humidity_target", 50)

# Energy metadata (postel má jen zásuvku, žádná baterie)
sel.set_attribute("energy", "type", "socket_load")
sel.set_attribute("energy", "power_w", 50)  # např. nabíječka telefonu

# Thermal (postel jako těleso)
sel.set_attribute("thermal", "type", "furniture")
sel.set_attribute("thermal", "thermal_mass_kg", 40)

# Safety
sel.set_attribute("safety", "fire_risk", false)
sel.set_attribute("safety", "exit_obstacle", false)
