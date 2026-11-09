import os
import shutil
import tempfile
import unittest

from pitwall.profile import load_profile
from pitwall.setup.draft import draft_to_toml, make_draft
from pitwall.setup.scan import autolog_key, humanize, scan, strip_comments
from pitwall.wpilog import WPILog
from tests.wpilog_writer import LogWriter

CONSTANTS = """
package frc.robot;
public final class Constants {
  public static final int SHOOTER_MAIN = 7;
  public static final int SHOOTER_FOLLOWER = 6;
  public static final int FEEDER = 4;   // the feeder
  public static final int OLD_THING = 30;
}
"""

SHOOTER = """
package frc.robot.subsystems;
public class Shooter extends SubsystemBase {
  private final TalonFX shooterMain = new TalonFX(Constants.SHOOTER_MAIN, CANBus.roboRIO()); // forward +
  private final TalonFX shooterFollower = new TalonFX(Constants.SHOOTER_FOLLOWER, CANBus.roboRIO());
  // private final TalonFX oldThing = new TalonFX(Constants.OLD_THING);
  /* private final TalonFX another = new TalonFX(31); */

  @AutoLogOutput
  private Current getMainSupplyCurrent() { return shooterMain.getSupplyCurrent().getValue(); }

  @AutoLogOutput(key = "Shooter/Follower/Amps")
  private double followerAmps() { return shooterFollower.getSupplyCurrent().getValueAsDouble(); }

  @AutoLogOutput
  private boolean isMainConnected() { return shooterMain.isConnected(); }

  @AutoLogOutput
  private boolean isHealthy() { return shooterMain.isConnected() && shooterFollower.isConnected(); }

  @AutoLogOutput
  private boolean getIsStalled() {
    return shooterMain.getStatorCurrent().getValueAsDouble() > 50 && shooterMain.getVelocity().getValueAsDouble() < 1;
  }

  @AutoLogOutput
  private double getMainVelocity() { return shooterMain.getVelocity().getValueAsDouble(); }

  public void periodic() {
    Logger.recordOutput("Shooter/FollowerVolts", shooterFollower.getMotorVoltage().getValueAsDouble());
    String url = "https://example.com/not//a/comment";
  }
}
"""

FEEDER = """
package frc.robot.subsystems;
public class Feeder extends SubsystemBase {
  private final TalonFXS m_feeder = new TalonFXS(Constants.FEEDER, "rio");
  @AutoLogOutput
  public Current getSupplyCurrent() { return m_feeder.getSupplyCurrent().getValue(); }
  @AutoLogOutput
  private boolean getIsHealthy() { return m_feeder.isConnected() && m_feeder.isAlive(); }
}
"""

HELPER = """
package frc.robot.util;
public class Wrapper {
  TalonFX motor;
  public Wrapper(int id, CANBus bus) { motor = new TalonFX(id, bus); }
}
"""

TUNER = """
package frc.robot.generated;
public class TunerConstants {
  public static final CANBus kCANBus = new CANBus("Drivetrain", "./logs/example.hoot");
  private static final int kFrontLeftDriveMotorId = 12;
  private static final int kFrontLeftSteerMotorId = 13;
  private static final int kFrontRightDriveMotorId = 23;
  private static final int kFrontRightSteerMotorId = 22;
  private static final int kBackLeftDriveMotorId = 11;
  private static final int kBackLeftSteerMotorId = 10;
  private static final int kBackRightDriveMotorId = 0;
  private static final int kBackRightSteerMotorId = 1;
}
"""

MODULE_IO = """
package frc.robot.subsystems.drive;
public interface ModuleIO {
  @AutoLog
  public static class ModuleIOInputs {
    public boolean driveConnected = false;
    public double driveVelocityRadPerSec = 0.0;
    public double driveAppliedVolts = 0.0;
    public double driveCurrentAmps = 0.0;
    public double driveSupplyCurrentAmps = 0.0;
    public boolean turnConnected = false;
    public double turnVelocityRadPerSec = 0.0;
    public double turnAppliedVolts = 0.0;
    public double turnCurrentAmps = 0.0;
    public double turnSupplyCurrentAmps = 0.0;
  }
}
"""

MODULE_IO_TALONFX = """
package frc.robot.subsystems.drive;
public class ModuleIOTalonFX implements ModuleIO {
  public ModuleIOTalonFX(SwerveModuleConstants constants) {
    driveTalon = new TalonFX(constants.DriveMotorId, TunerConstants.kCANBus);
    turnTalon = new TalonFX(constants.SteerMotorId, TunerConstants.kCANBus);
  }
}
"""

MODULE = """
package frc.robot.subsystems.drive;
public class Module {
  public void periodic() { Logger.processInputs("Drive/Module" + Integer.toString(index), inputs); }
}
"""

CONTAINER = """
package frc.robot;
public class RobotContainer {
  public RobotContainer() {
    drive = new Drive(gyro,
        new ModuleIOTalonFX(TunerConstants.FrontLeft),
        new ModuleIOTalonFX(TunerConstants.FrontRight),
        new ModuleIOTalonFX(TunerConstants.BackLeft),
        new ModuleIOTalonFX(TunerConstants.BackRight));
  }
}
"""


def make_project(root):
    files = {
        "build.gradle": "plugins {}",
        "src/main/java/frc/robot/Constants.java": CONSTANTS,
        "src/main/java/frc/robot/RobotContainer.java": CONTAINER,
        "src/main/java/frc/robot/subsystems/Shooter.java": SHOOTER,
        "src/main/java/frc/robot/subsystems/Feeder.java": FEEDER,
        "src/main/java/frc/robot/util/Wrapper.java": HELPER,
        "src/main/java/frc/robot/generated/TunerConstants.java": TUNER,
        "src/main/java/frc/robot/subsystems/drive/ModuleIO.java": MODULE_IO,
        "src/main/java/frc/robot/subsystems/drive/ModuleIOTalonFX.java": MODULE_IO_TALONFX,
        "src/main/java/frc/robot/subsystems/drive/Module.java": MODULE,
    }
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        make_project(self.dir)
        self.r = scan(self.dir)
        self.by = {m.name: m for m in self.r.motors}

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_helpers(self):
        self.assertEqual(humanize("m_intakeRollerMainLeft"), "Intake roller main left")
        self.assertEqual(humanize("HotTub"), "Hot tub")
        self.assertEqual(autolog_key("Shooter", "getMainSupplyCurrent", ""), "/RealOutputs/Shooter/MainSupplyCurrent")
        self.assertEqual(autolog_key("Shooter", "isMainConnected", ""), "/RealOutputs/Shooter/IsMainConnected")
        self.assertEqual(autolog_key("X", "f", 'key = "A/B"'), "/RealOutputs/A/B")
        self.assertIn('"https://example.com/not//a/comment"', strip_comments(SHOOTER))

    def test_motors(self):
        self.assertEqual(sorted(self.by), sorted([
            "Shooter main", "Shooter follower", "Feeder",
            "Drive FL", "Steer FL", "Drive FR", "Steer FR", "Drive BL", "Steer BL", "Drive BR", "Steer BR"]))
        self.assertEqual(self.by["Shooter main"].can_id, 7)
        self.assertEqual(self.by["Feeder"].controller, "TalonFXS")
        self.assertEqual(self.by["Drive BR"].can_id, 0)
        self.assertEqual(self.by["Steer FR"].bus, "Drivetrain")
        self.assertEqual(self.r.notes, [])

    def test_keys(self):
        m = self.by["Shooter main"].keys
        self.assertEqual(m["supply"], "/RealOutputs/Shooter/MainSupplyCurrent")
        self.assertEqual(m["connected"], "/RealOutputs/Shooter/IsMainConnected")   # not the combined flag
        self.assertEqual(m["velocity"], "/RealOutputs/Shooter/MainVelocity")       # not IsStalled
        self.assertNotIn("stator", m)                                              # IsStalled is a test, not a reading
        f = self.by["Shooter follower"].keys
        self.assertEqual(f["supply"], "/RealOutputs/Shooter/Follower/Amps")
        self.assertEqual(f["voltage"], "/RealOutputs/Shooter/FollowerVolts")
        self.assertNotIn("connected", self.by["Feeder"].keys)                      # IsHealthy also needs isAlive
        self.assertEqual(self.by["Feeder"].keys["supply"], "/RealOutputs/Feeder/SupplyCurrent")
        d = self.by["Drive BL"].keys
        self.assertEqual(d["supply"], "/Drive/Module2/DriveSupplyCurrentAmps")
        self.assertEqual(d["connected"], "/Drive/Module2/DriveConnected")
        self.assertEqual(self.by["Steer FL"].keys["voltage"], "/Drive/Module0/TurnAppliedVolts")

    def test_draft_with_log(self):
        log_path = os.path.join(self.dir, "akit_26-01-01_10-00-00_x_q1.wpilog")
        w = LogWriter(log_path)
        w.put("/SystemStats/BatteryVoltage", 0.0, 12.0)
        w.put("/RealOutputs/Shooter/MainSupplyCurrent", 0.0, 1.0)
        w.put("/RealOutputs/Shooter/FollowerSupplyCurrent", 0.0, 1.0)     # code says Follower/Amps: renamed
        w.put("/RealOutputs/Feeder/SupplyCurrent", 0.0, 1.0)
        w.put("/RealOutputs/Climber/LiftSupplyCurrent", 0.0, 1.0)          # a motor the code reader missed
        w.put("/RealOutputs/RobotContainer/IsShooting", 0.0, False)
        for i in range(4):
            w.put(f"/Drive/Module{i}/DriveSupplyCurrentAmps", 0.0, 1.0)
            w.put(f"/Drive/Module{i}/TurnSupplyCurrentAmps", 0.0, 1.0)
            w.put(f"/Drive/Module{i}/DriveVelocityRadPerSec", 0.0, 0.0)
        w.close()
        d = make_draft(self.r, WPILog(log_path), "Test")
        st = {m["name"]: m["status"]["supply"] for m in d["motors"]}
        self.assertEqual(st["Shooter main"], "traced")
        self.assertEqual(st["Shooter follower"], "named")
        self.assertEqual(next(m for m in d["motors"] if m["name"] == "Shooter follower")["keys"]["supply"],
                         "/RealOutputs/Shooter/FollowerSupplyCurrent")
        self.assertEqual(d["unclaimed"], ["/RealOutputs/Climber/LiftSupplyCurrent"])
        self.assertEqual(d["shooting_key"], "/RealOutputs/RobotContainer/IsShooting")
        self.assertEqual(d["motors"][0]["subsystem"], "Drive")
        self.assertEqual(d["subsystems"][0]["name"], "Drive")
        self.assertEqual(d["driving_keys"], [f"/Drive/Module{i}/DriveVelocityRadPerSec" for i in range(4)])
        # review: merge, drop one, mark a group as shooting-only; then it must load as a profile
        for m in d["motors"]:
            if m["subsystem"] == "Steer":
                m["subsystem"] = "Drive"
        d["subsystems"] = [{"name": "Shooter", "needed_in": ["Shooting"]}]
        d["motors"][-1]["include"] = False
        self.assertEqual(d["game"], "quarters")          # no game data in this log
        d["game"] = "rebuilt-2026"
        path = os.path.join(self.dir, "p.toml")
        with open(path, "w") as f:
            f.write(draft_to_toml(d))
        p = load_profile(path)
        self.assertEqual(p.name, "Test")
        self.assertEqual(len(p.motors), 10)
        self.assertEqual([s.name for s in p.subsystems][:1], ["Drive"])
        self.assertEqual(next(s for s in p.subsystems if s.name == "Shooter").needed_in, ["Shooting"])
        self.assertEqual(len(p.periods), 7)

    def test_no_motors_is_an_error(self):
        with self.assertRaises(ValueError):
            draft_to_toml({"motors": [], "subsystems": []})


if __name__ == "__main__":
    unittest.main()
