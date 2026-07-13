# Data layout

収録した生データは `data/raw/`、前処理後のSolist-AI Sim用データは
`data/processed/` に置きます。`raw/` は容量が大きくなるためGit管理外です。

各打撃には、少なくとも `session_id`, `hit_id`, `x_mm`, `y_mm` と3軸加速度波形を
対応付けます。学習・検証・テストの分割は `session_id` 単位で行います。

