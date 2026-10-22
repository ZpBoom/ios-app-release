# Execute lane definitions against an inert DSL; no Xcode or Apple requests.
require "json"
require "tmpdir"

class LaneHarness
  attr_reader :calls
  def initialize
    @lanes = {}
    @calls = []
  end
  def default_platform(*)
  end
  def platform(*)
    yield
  end
  def lane(name, &block)
    @lanes[name] = block
  end
  def run(name)
    @lanes.fetch(name).call
  end
  def build_app(**options)
    @calls << [:archive, options]
  end
  def app_store_connect_api_key(**options)
    {test_key: true}
  end
  def upload_to_testflight(**options)
    @calls << [:upload, options]
  end
  def upload_to_app_store(**options)
    @calls << [:submit, options]
  end
end

Dir.mktmpdir do |dir|
  config = {"bundle_id" => "com.example.app", "app_store_id" => "123", "source" => dir,
    "workspace" => "App.xcworkspace", "scheme" => "App", "team_id" => "ABCDE12345",
    "version" => "1.2.0", "build" => "120", "output" => dir,
    "export_options" => {"method" => "app-store-connect"}}
  path = File.join(dir, "runtime.json")
  File.write(path, JSON.generate(config))
  ENV["IOS_RELEASE_CONFIG"] = path
  ENV["ASC_KEY_ID"] = "test"
  ENV["ASC_KEY_PATH"] = "/not-read/test.p8"
  h = LaneHarness.new
  file = File.expand_path("../fastlane/Fastfile", __dir__)
  h.instance_eval(File.read(file), file)
  [:archive, :upload, :submit].each { |lane| h.run(lane) }
  archive, upload, submit = h.calls.map(&:last)
  raise "wrong workspace" unless archive[:workspace] == File.join(dir, "App.xcworkspace")
  raise "wrong version flags" unless archive[:xcargs].include?("CURRENT_PROJECT_VERSION=120")
  raise "upload unexpectedly distributes build" unless upload[:skip_submission] == true
  raise "wrong target build" unless submit[:build_number] == "120" && submit[:app_version] == "1.2.0"
  raise "automatic release enabled" unless submit[:automatic_release] == false
  raise "submission rewrites assets" unless submit[:skip_metadata] && submit[:skip_screenshots] && submit[:skip_binary_upload]
end
puts "Fastfile lane behavior checks passed (no external actions)."
