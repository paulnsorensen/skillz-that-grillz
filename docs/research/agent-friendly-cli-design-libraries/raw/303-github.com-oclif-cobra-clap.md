# Framework source checks via gh api (fetched 2026-09-23)
oclif/core src/command.ts:69: public static enableJsonFlag = false
oclif/core src/command.ts:183: if (result && this.jsonEnabled()) this.logJson(this.toSuccessJson(result))
spf13/cobra site/content/user_guide.md:779: Cobra will print automatic suggestions when "unknown command" errors happen.
spf13/cobra site/content/user_guide.md:791: Suggestions are automatically generated based on existing subcommands and use an implementation of Levenshtein distance.
spf13/cobra site/content/user_guide.md:796: command.DisableSuggestions = true
clap-rs/clap clap_builder/Cargo.toml:32: default = ["std", "color", "help", "usage", "error-context", "suggestions"]
clap-rs/clap clap_builder/Cargo.toml:42: suggestions = ["dep:strsim", "error-context"]
