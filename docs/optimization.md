# Optimisation

At every control step, AstraGrid calls `scipy.optimize.milp` with a binary variable for each eligible task. The constraint limits optional task power to available bus source plus reserve-safe battery discharge minus critical demand. Objective value is task priority, with a hard-deadline bonus. The controller never schedules a task after its deadline. The independent validator recomputes first-step demand, supply, reserve/rate and resource safeguards before any action is executed.

This is a deliberately compact rolling-horizon prototype: it replans every step rather than claiming long-range aerospace-grade optimality.
