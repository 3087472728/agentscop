$c = Get-Content "$env:USERPROFILE\.docker\config.json" -Raw | ConvertFrom-Json
"auths: " + ($c.auths.PSObject.Properties.Name -join ', ')
"credsStore: " + $c.credsStore
