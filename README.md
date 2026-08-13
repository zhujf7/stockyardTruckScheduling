# Conflict-free Scheduling of Autonomous Trucks in Complex Dry Bulk Terminal Stockyards

## Dataset

`data/input/map_local.json`: Guide-path map of the dry bulk terminal
stockyard. The original GPS trajectories have been converted to anonymized local Cartesian coordinates.

`data/instances/static/`: Twelve static scheduling instances containing 20,
30, 40, and 50 vehicles with arrival horizons of 300, 600, and 1200 seconds.

`data/instances/rolling/instance_250_14400.json`: Rolling-horizon scheduling
instance containing 250 vehicles arriving over 14,400 seconds.

`data/preprocessed/all_routes.json`: Feasible inbound and outbound routes
between the entry node and unloading operation nodes.

`data/preprocessed/conflict_pairs.json`: 87,170 pairs of directional path
operations whose vehicle swept areas conflict.

## Example results

`results/static/`: Illustrative scheduling examples obtained using FCFS,
Gurobi, and IFO. 
`results/rolling/`: Example rolling-horizon scheduling results.

## Visualization

Run the visualization by specifying a scheduling-result JSON file:

```powershell
python main.py visualization `
  --schedule results/static/ifo/instance_40_600.json `
  --open
```

The generated HTML viewer is saved in `output/`, and its path is printed in
the terminal. For the command above, the output file is:

```text
output/static_ifo_instance_40_600.html
```
