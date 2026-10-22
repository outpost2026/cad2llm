# frozen_string_literal: true
#
# Test runner: ruby spec/run_all.rb
# Runs every spec/test_*.rb in a separate process and aggregates the exit code.
#
here = __dir__
files = Dir[File.join(here, "test_*.rb")].sort
failed = []

files.each do |file|
  puts "\n=== #{File.basename(file)} " + "=" * (60 - File.basename(file).length)
  ok = system(RbConfig.ruby, file)
  failed << File.basename(file) unless ok
end

puts
if failed.empty?
  puts "SUITE OK (#{files.size} files)"
  exit 0
else
  puts "SUITE FAILED: #{failed.join(', ')}"
  exit 1
end
